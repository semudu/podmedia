import sys
import os
import platform

APP_NAME = "PodMedia"

def get_executable_command():
    """Returns the python executable command for background launch."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app_py = os.path.join(base_dir, "app.py")
    
    # Use pythonw.exe on Windows if available to hide console window
    python_exe = sys.executable
    if platform.system() == "Windows" and python_exe.endswith("python.exe"):
        pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.exists(pythonw):
            python_exe = pythonw
            
    return f'"{python_exe}" "{app_py}"'

def is_autostart_enabled():
    """Checks if autostart is currently enabled for the current OS."""
    system = platform.system()
    
    if system == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    elif system == "Darwin": # macOS
        plist_path = os.path.expanduser(f"~/Library/LaunchAgents/com.podmedia.app.plist")
        return os.path.exists(plist_path)

    elif system == "Linux":
        desktop_path = os.path.expanduser(f"~/.config/autostart/podmedia.desktop")
        return os.path.exists(desktop_path)

    return False

def set_autostart(enable=True):
    """Enables or disables autostart on system boot."""
    system = platform.system()
    cmd = get_executable_command()

    if system == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                print(f"[✓] Windows autostart enabled: {APP_NAME}")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    print(f"[✓] Windows autostart disabled: {APP_NAME}")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except Exception as e:
            print("Windows registry autostart error:", e)
            return False

    elif system == "Darwin": # macOS
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.podmedia.app.plist")
        if enable:
            content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.podmedia.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.py')}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
            os.makedirs(os.path.dirname(plist_path), exist_ok=True)
            with open(plist_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("[✓] macOS LaunchAgent autostart enabled.")
        else:
            if os.path.exists(plist_path):
                os.remove(plist_path)
                print("[✓] macOS LaunchAgent autostart disabled.")
        return True

    elif system == "Linux":
        desktop_path = os.path.expanduser("~/.config/autostart/podmedia.desktop")
        if enable:
            content = f"""[Desktop Entry]
Type=Application
Name=PodMedia Hub
Exec={cmd}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=PodMedia Containerized Media Suite
"""
            os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("[✓] Linux autostart entry created.")
        else:
            if os.path.exists(desktop_path):
                os.remove(desktop_path)
                print("[✓] Linux autostart entry removed.")
        return True

    return False
