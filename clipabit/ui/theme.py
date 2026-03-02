import platform

class Theme:
    """Color theme for the UI matching Figma design."""
    
    # Dark theme (default) - Updated to match Figma mockups
    DARK = {
        'background': '#26272E',
        'card_bg': '#3A3B42',
        'card_thumbnail': '#D9D9D9',
        'text': '#FFFFFF',
        'text_secondary': '#9CA3AF',
        'accent': '#FAAF04',
        'accent_hover': '#E09A00',
        'search_bg': '#F2F8FF',
        'search_text': '#000000',
        'search_placeholder': '#9CA3AF',
        'button_text': '#FFFFFF',
        'button_secondary': '#9CA3AF',
        'border': '#4A4B52',
        'progress_bg': '#FAAF04',
        'close_button': '#FFFFFF',
        'logo_bg': '#FFFFFF',
        'logo_inner': '#979797',
        'logo_play': '#26272E',
    }
    
    # Light theme - Updated to match Figma mockups
    LIGHT = {
        'background': '#FFFFFF',
        'card_bg': '#F5F5F5',
        'card_thumbnail': '#D9D9D9',
        'text': '#0F1729',
        'text_secondary': '#9CA3AF',
        'accent': '#FAAF04',
        'accent_hover': '#E09A00',
        'accent_border': '#189FD1',
        'search_bg': '#F2F8FF',
        'search_text': '#000000',
        'search_placeholder': '#9CA3AF',
        'button_text': '#FFFFFF',
        'button_secondary': '#9CA3AF',
        'border': '#189FD1',
        'progress_bg': '#FAAF04',
        'close_button': '#0F1729',
        'logo_bg': '#0F1729',
        'logo_inner': '#979797',
        'logo_play': '#FFFFFF',
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
