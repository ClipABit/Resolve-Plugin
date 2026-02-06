import platform

class Theme:
    """Color theme for the UI matching Figma design."""
    
    # Dark theme (default)
    DARK = {
        'background': '#26272E',
        'card_bg': '#3A3B42',
        'text': '#FFFFFF',
        'text_secondary': '#8E8E93',
        'accent': '#F5A623',
        'accent_hover': '#E09000',
        'search_bg': '#F2F8FF',
        'search_text': '#000000',
        'search_placeholder': '#8E8E93',
        'button_text': '#000000',
        'border': '#4A4B52',
        'light_border': '#4A4B52',
        'welcome_text': '#8E8E93',
        'code_bg': '#D9D9D9',
        'step_badge_bg': '#F5A623',
        'step_badge_text': '#000000',
    }
    
    # Light theme
    LIGHT = {
        'background': '#FFFFFF',
        'card_bg': '#E5E5E5',
        'text': '#000000',
        'text_secondary': '#8E8E93',
        'accent': '#F5A623',
        'accent_hover': '#E09000',
        'search_bg': '#F2F8FF',
        'search_text': '#000000',
        'search_placeholder': '#8E8E93',
        'button_text': '#000000',
        'border': '#D0D0D0',
        'light_border': '#189FD1',
        'welcome_text': '#8E8E93',
        'code_bg': '#D9D9D9',
        'step_badge_bg': '#F5A623',
        'step_badge_text': '#000000',
    }
    
    # Current theme (can be toggled or auto-detected)
    current = DARK
    
    @classmethod
    def detect_system_theme(cls):
        """Detect system theme (macOS/Windows) and set current theme accordingly."""
        try:
            if platform.system() == 'Darwin':  # macOS
                # Check macOS appearance setting
                import subprocess
                result = subprocess.run(
                    ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                    capture_output=True, text=True
                )
                # If 'Dark' is returned, system is in dark mode
                # If command fails (exit code != 0), system is in light mode
                if result.returncode == 0 and 'Dark' in result.stdout:
                    cls.current = cls.DARK
                else:
                    cls.current = cls.LIGHT
            elif platform.system() == 'Windows':
                # Check Windows registry for theme
                import winreg
                registry = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
                key = winreg.OpenKey(registry, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize')
                value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
                cls.current = cls.LIGHT if value == 1 else cls.DARK
            else:
                # Default to dark for Linux/other
                cls.current = cls.DARK
        except Exception as e:
            print(f"Could not detect system theme: {e}, defaulting to dark")
            cls.current = cls.DARK
