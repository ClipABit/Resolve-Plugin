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
        'upload_zone_bg': '#292C38',
        'upload_zone_border': '#7B8CA0',
        'browse_btn_bg': '#FAAF04',
        'browse_btn_text': '#FFFFFF',
    }
    
    # Current theme (always dark)
    current = DARK
    
    @classmethod
    def detect_system_theme(cls):
        """Force dark mode regardless of system appearance."""
        cls.current = cls.DARK
