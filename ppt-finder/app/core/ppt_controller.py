import ctypes
import logging
import os
import queue
import threading
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)


class PowerPointUnavailable(RuntimeError):
    """A recoverable failure to communicate with the presentation application."""


_jobs = queue.Queue(maxsize=1)
_operation_lock = threading.Lock()
_worker_lock = threading.Lock()
_worker = None
_status_lock = threading.Lock()
_status_cache = None
_status_time = 0.0
_slide_cache = {}
_current_deadline = 0.0


def _powerpoint_window():
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    return user32.FindWindowW("PP12FrameClass", None)


def is_powerpoint_running():
    """Detect PowerPoint without starting an Office process or stealing focus."""
    try:
        return bool(_powerpoint_window())
    except Exception:
        return False


def bring_powerpoint_to_front():
    """Activate PowerPoint only after an explicit open action."""
    try:
        hwnd = _powerpoint_window()
        if hwnd:
            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        logger.debug("Could not activate the PowerPoint window", exc_info=True)
    return False


def _com_worker():
    """Own every COM object on one apartment; never pass proxies to Flask threads."""
    global _current_deadline
    pythoncom = None
    initialization_error = None
    try:
        import pythoncom as com
        com.CoInitialize()
        pythoncom = com
    except Exception as exc:
        initialization_error = exc

    try:
        while True:
            job = _jobs.get()
            try:
                if time.monotonic() >= job["deadline"]:
                    raise PowerPointUnavailable("PowerPoint request expired before it could start")
                if initialization_error is not None:
                    raise PowerPointUnavailable("PowerPoint COM is unavailable on this computer") from initialization_error
                _current_deadline = job["deadline"]
                job["result"] = job["callback"](*job["args"])
            except Exception as exc:
                # Exception tracebacks can retain COM proxies. Cross the thread
                # boundary with plain text only, and release proxies here.
                job["error"] = str(exc)
                logger.debug("PowerPoint operation failed", exc_info=True)
            finally:
                _current_deadline = 0.0
                # Do not release this slot when the caller times out. An Office
                # dialog can block COM; retries must not queue duplicate actions.
                _operation_lock.release()
                job["done"].set()
                _jobs.task_done()
    finally:
        if pythoncom is not None:
            pythoncom.CoUninitialize()


def _call_com(callback, *args, timeout=4.0):
    global _worker
    if not _operation_lock.acquire(blocking=False):
        raise PowerPointUnavailable("PowerPoint is still processing a request. Wait for it to finish before trying again.")
    submitted = False
    try:
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_com_worker, name="powerpoint-com", daemon=True)
                _worker.start()
        job = {
            "callback": callback,
            "args": args,
            "deadline": time.monotonic() + timeout,
            "done": threading.Event(),
        }
        _jobs.put_nowait(job)
        submitted = True
        if not job["done"].wait(timeout):
            # COM itself cannot safely be cancelled. The single worker remains
            # occupied, preventing queued or duplicated navigation commands.
            raise PowerPointUnavailable("PowerPoint has not responded yet. The request may still complete; check PowerPoint before repeating it.")
        if "error" in job:
            raise PowerPointUnavailable(job["error"])
        return job["result"]
    finally:
        if not submitted:
            _operation_lock.release()


def _check_action_deadline():
    if _current_deadline and time.monotonic() >= _current_deadline:
        raise PowerPointUnavailable("PowerPoint request expired. No further action was sent.")


def _active_powerpoint():
    import win32com.client
    try:
        return win32com.client.GetActiveObject("PowerPoint.Application")
    except Exception as exc:
        _slide_cache.clear()
        raise PowerPointUnavailable("PowerPoint is not running or is busy") from exc


def _active_presentation(ppt):
    # A slideshow on the audience display can remain active while an operator
    # edits or previews another deck. Remote navigation must follow the show.
    try:
        if ppt.SlideShowWindows.Count:
            return ppt.SlideShowWindows(1).Presentation
    except Exception:
        pass
    try:
        return ppt.ActivePresentation
    except Exception as exc:
        raise PowerPointUnavailable("No active PowerPoint presentation") from exc


def _slideshow_window(pres):
    try:
        return pres.SlideShowWindow
    except Exception:
        return None


def _slide_text(shape):
    """Keep one unsupported shape from hiding all remaining slide content."""
    parts = []
    try:
        if shape.HasTextFrame and shape.TextFrame.HasText:
            parts.append(shape.TextFrame.TextRange.Text.strip())
        if shape.HasTable:
            table = shape.Table
            for row in range(1, table.Rows.Count + 1):
                parts.append(" | ".join(table.Cell(row, col).Shape.TextFrame.TextRange.Text.strip()
                                        for col in range(1, table.Columns.Count + 1)))
        if shape.Type == 6:  # msoGroup
            for child in shape.GroupItems:
                parts.extend(_slide_text(child))
    except Exception:
        logger.debug("Could not read one PowerPoint shape", exc_info=True)
    return [part.replace("\r\n", "\n").replace("\r", "\n").replace("\v", "\n") for part in parts if part]


def _read_status():
    ppt = _active_powerpoint()
    pres = _active_presentation(ppt)
    ssw = _slideshow_window(pres)
    count = int(pres.Slides.Count)
    current = 1 if count else 0
    if ssw is not None:
        current = int(ssw.View.Slide.SlideIndex)
    else:
        try:
            current = int(ppt.ActiveWindow.View.Slide.SlideIndex)
        except Exception:
            pass

    key = (str(pres.FullName), count)
    cached = _slide_cache.get(key)
    now = time.monotonic()
    if cached is None or now - cached[0] > 15:
        slides = []
        for idx, slide in enumerate(pres.Slides, 1):
            lines = []
            for shape in slide.Shapes:
                lines.extend(_slide_text(shape))
            title = lines[0].replace("\r", "\n").split("\n")[0][:80] if lines else f"Slide {idx}"
            slides.append({"index": idx, "title": title, "label": f"Slide {idx}", "text": "\n".join(lines)})
        _slide_cache.clear()
        _slide_cache[key] = (now, slides)
    else:
        slides = cached[1]
    show_state = int(ssw.View.State) if ssw is not None else 1
    return {
        "running": True,
        "title": str(pres.Name),
        "slideshow_active": ssw is not None,
        "total_slides": count,
        "current_slide_index": current,
        "blackout": show_state == 3,  # ppSlideShowBlackScreen
        "clear_text": False,
        "slides": slides,
    }


def get_powerpoint_slideshow_status():
    """Bounded status request; polling never launches or activates PowerPoint."""
    global _status_cache, _status_time
    with _status_lock:
        if _status_cache is not None and time.monotonic() - _status_time < 0.75:
            return dict(_status_cache)
    try:
        status = _call_com(_read_status, timeout=2.5)
    except Exception as exc:
        return {"running": False, "slideshow_active": False, "error": str(exc)}
    with _status_lock:
        _status_cache = status
        _status_time = time.monotonic()
    return dict(status)


def _invalidate_status():
    global _status_cache, _status_time
    with _status_lock:
        _status_cache = None
        _status_time = 0.0


def _open_presentation(path):
    import win32com.client
    try:
        ppt = win32com.client.GetActiveObject("PowerPoint.Application")
    except Exception:
        _check_action_deadline()
        ppt = win32com.client.Dispatch("PowerPoint.Application")
    _check_action_deadline()
    ppt.Visible = True
    normalized = os.path.normcase(os.path.abspath(path))
    presentation = None
    for existing in ppt.Presentations:
        if os.path.normcase(os.path.abspath(existing.FullName)) == normalized:
            presentation = existing
            break
    if presentation is None:
        _check_action_deadline()
        presentation = ppt.Presentations.Open(os.path.abspath(path))
    _check_action_deadline()
    try:
        presentation.Windows(1).Activate()
        ppt.Activate()
    except Exception:
        logger.debug("Presentation opened but window activation was refused", exc_info=True)
    bring_powerpoint_to_front()
    _slide_cache.clear()
    _invalidate_status()
    return "Presentation opened in PowerPoint"


def open_powerpoint_presentation(path):
    """Explicit server-mode opening; desktop opening uses the OS association."""
    if not os.path.isfile(path):
        return False, "Presentation file does not exist"
    try:
        return True, _call_com(_open_presentation, path, timeout=12.0)
    except Exception as exc:
        return False, str(exc)


def _control_slideshow(action, slide_index):
    ppt = _active_powerpoint()
    pres = _active_presentation(ppt)
    ssw = _slideshow_window(pres)
    if action == "close":
        if not pres.Saved:
            raise PowerPointUnavailable("The presentation has unsaved changes. Save or close it in PowerPoint.")
        if ssw is not None:
            _check_action_deadline()
            ssw.View.Exit()
        _check_action_deadline()
        pres.Close()
        _slide_cache.clear()
        return "Presentation closed"
    if action == "start":
        if ssw is None:
            _check_action_deadline()
            pres.SlideShowSettings.Run()
        return "Slideshow started"
    if ssw is None:
        raise PowerPointUnavailable("No active PowerPoint slideshow")
    if action == "next":
        _check_action_deadline()
        ssw.View.Next()
        return "Next slide"
    if action in ("back", "prev"):
        _check_action_deadline()
        ssw.View.Previous()
        return "Previous slide"
    if action == "stop":
        _check_action_deadline()
        ssw.View.Exit()
        return "Slideshow stopped"
    if action == "blackout_toggle":
        state = int(ssw.View.State)
        _check_action_deadline()
        ssw.View.State = 1 if state == 3 else 3
        return "PowerPoint blackout changed"
    if not 1 <= slide_index <= pres.Slides.Count:
        raise PowerPointUnavailable("Slide index out of range")
    _check_action_deadline()
    ssw.View.GotoSlide(slide_index)
    return f"Navigated to slide {slide_index}"


def control_powerpoint_slideshow(action, slide_index=None):
    """Apply exactly one COM action; never broadcast simulated keys to other apps."""
    if action not in {"start", "next", "back", "prev", "stop", "close", "goto", "blackout_toggle"}:
        return False, f"Unsupported PowerPoint action: {action}"
    if action == "goto":
        if isinstance(slide_index, bool):
            return False, "A whole slide number is required"
        try:
            number = int(slide_index)
            if str(number) != str(slide_index).strip() or number < 1:
                raise ValueError
            slide_index = number
        except (TypeError, ValueError, OverflowError):
            return False, "A whole slide number is required"
    try:
        return True, _call_com(_control_slideshow, action, slide_index, timeout=6.0)
    except Exception as exc:
        return False, str(exc)
    finally:
        _invalidate_status()
