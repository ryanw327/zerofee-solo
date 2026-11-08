"""Small native Windows notification icon. Uses the Tk thread's Windows message pump."""
import ctypes as C
from ctypes import wintypes as W
import os

LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

class WNDCLASS(C.Structure):
    _fields_ = [('style', W.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', C.c_int),
        ('cbWndExtra', C.c_int), ('hInstance', W.HINSTANCE), ('hIcon', W.HICON),
        ('hCursor', W.HANDLE), ('hbrBackground', W.HBRUSH), ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR)]

class NOTIFYICONDATA(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('uID', W.UINT), ('uFlags', W.UINT),
        ('uCallbackMessage', W.UINT), ('hIcon', W.HICON), ('szTip', W.WCHAR * 128),
        ('dwState', W.DWORD), ('dwStateMask', W.DWORD), ('szInfo', W.WCHAR * 256),
        ('uVersion', W.UINT), ('szInfoTitle', W.WCHAR * 64), ('dwInfoFlags', W.DWORD),
        ('guidItem', C.c_byte * 16), ('hBalloonIcon', W.HICON)]

class Tray:
    def __init__(self, icon_path, dispatch):
        self.dispatch, self.active, self.present = dispatch, False, False
        self.hwnd = self.icon = None
        self.user = C.WinDLL('user32', use_last_error=True)
        self.shell = C.WinDLL('shell32', use_last_error=True)
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        def bind(lib, name, result, args):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args
            return fn
        bind(self.kernel, 'GetModuleHandleW', W.HMODULE, [W.LPCWSTR])
        bind(self.user, 'RegisterClassW', W.WORD, [C.POINTER(WNDCLASS)])
        bind(self.user, 'UnregisterClassW', W.BOOL, [W.LPCWSTR, W.HINSTANCE])
        bind(self.user, 'CreateWindowExW', W.HWND, [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
             C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p])
        bind(self.user, 'DefWindowProcW', LRESULT, [W.HWND, W.UINT, W.WPARAM, W.LPARAM])
        bind(self.user, 'DestroyWindow', W.BOOL, [W.HWND])
        bind(self.user, 'RegisterWindowMessageW', W.UINT, [W.LPCWSTR])
        bind(self.user, 'LoadImageW', W.HANDLE, [W.HINSTANCE, W.LPCWSTR, W.UINT, C.c_int, C.c_int, W.UINT])
        bind(self.user, 'DestroyIcon', W.BOOL, [W.HICON])
        bind(self.user, 'CreatePopupMenu', W.HMENU, [])
        bind(self.user, 'AppendMenuW', W.BOOL, [W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR])
        bind(self.user, 'DestroyMenu', W.BOOL, [W.HMENU])
        bind(self.user, 'TrackPopupMenu', W.UINT, [W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p])
        bind(self.user, 'GetCursorPos', W.BOOL, [C.POINTER(W.POINT)])
        bind(self.user, 'SetForegroundWindow', W.BOOL, [W.HWND])
        bind(self.user, 'PostMessageW', W.BOOL, [W.HWND, W.UINT, W.WPARAM, W.LPARAM])
        bind(self.shell, 'Shell_NotifyIconW', W.BOOL, [W.DWORD, C.POINTER(NOTIFYICONDATA)])
        self.instance = self.kernel.GetModuleHandleW(None)
        self.name = 'BitcoinMinerTray_' + str(os.getpid())
        self.restart_message = self.user.RegisterWindowMessageW('TaskbarCreated')
        self.callback_message = 0x8001
        self.proc = WNDPROC(self._message)
        self.wc = WNDCLASS(lpfnWndProc=self.proc, hInstance=self.instance, lpszClassName=self.name)
        if not self.user.RegisterClassW(C.byref(self.wc)):
            raise C.WinError(C.get_last_error())
        self.hwnd = self.user.CreateWindowExW(0, self.name, 'ZeroFee Solo tray host', 0,
                                              0, 0, 0, 0, None, None, self.instance, None)
        self.icon = self.user.LoadImageW(None, str(icon_path), 1, 16, 16, 0x10)
        if not self.hwnd or not self.icon:
            self.close()
            raise RuntimeError('Could not create the tray window or load the pickaxe icon.')
        self.data = NOTIFYICONDATA(cbSize=C.sizeof(NOTIFYICONDATA), hWnd=self.hwnd, uID=1,
            uFlags=1 | 2 | 4, uCallbackMessage=self.callback_message, hIcon=self.icon, szTip='ZeroFee Solo - Idle')
        self.present = bool(self.shell.Shell_NotifyIconW(0, C.byref(self.data)))
        if not self.present:
            self.close()
            raise RuntimeError('Windows did not accept the tray icon. The window will stay accessible.')

    def _message(self, hwnd, message, wparam, lparam):
        try:
            if message == self.restart_message and hasattr(self, 'data'):
                self.present = bool(self.shell.Shell_NotifyIconW(0, C.byref(self.data)))
                if not self.present:
                    self.dispatch('tray_failed')
            elif message == self.callback_message:
                if lparam in (0x202, 0x203):  # left release or double click
                    self.dispatch('open')
                elif lparam in (0x205, 0x7b):
                    self._menu()
        except Exception:
            self.dispatch('tray_failed')
        return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

    def _menu(self):
        menu = self.user.CreatePopupMenu()
        try:
            for command, label, disabled in [(1, 'Open ZeroFee Solo', False),
                    (2, 'Start selected mode...', self.active), (3, 'Stop', not self.active),
                    (4, 'Exit (stop safely)', False)]:
                self.user.AppendMenuW(menu, 1 if disabled else 0, command, label)
            point = W.POINT()
            self.user.GetCursorPos(C.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            chosen = self.user.TrackPopupMenu(menu, 0x100 | 2, point.x, point.y, 0, self.hwnd, None)
            if chosen:
                self.dispatch({1: 'open', 2: 'start', 3: 'stop', 4: 'exit'}[chosen])
            self.user.PostMessageW(self.hwnd, 0, 0, 0)
        finally:
            self.user.DestroyMenu(menu)

    def update(self, status, active):
        self.active = active
        self.data.szTip = ('ZeroFee Solo - ' + status)[:127]
        if self.present and not self.shell.Shell_NotifyIconW(1, C.byref(self.data)):
            self.present = False
            self.dispatch('tray_failed')

    def close(self):
        if self.present and hasattr(self, 'data'):
            self.shell.Shell_NotifyIconW(2, C.byref(self.data))
        self.present = False
        if getattr(self, 'hwnd', None):
            self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
        if getattr(self, 'icon', None):
            self.user.DestroyIcon(self.icon)
            self.icon = None
        self.user.UnregisterClassW(self.name, self.instance)
