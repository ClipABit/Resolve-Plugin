import sys
import os

# Add the current directory to sys.path so we can import the 'clipabit' package
# This dynamic detection works in both standalone Python and DaVinci Resolve
import inspect

try:
    # Standard Python environment
    script_path = os.path.abspath(__file__)
except NameError:
    # DaVinci Resolve environment (where __file__ is undefined)
    try:
        script_path = os.path.abspath(inspect.getfile(inspect.currentframe()))
    except Exception:
        # Fallback to current working directory if all else fails
        script_path = os.path.abspath(".")

current_dir = os.path.dirname(script_path)

# Smart Import Logic:
# 1. Check current directory (simple co-located install)
# 2. Check Fusion/Modules directory (clean install, hidden from menus)
#    Relative path from Scripts/Utility to Modules is ../../Modules
search_paths = [
    current_dir,
    os.path.abspath(os.path.join(current_dir, "..", "..", "Modules"))
]

package_found = False
for path in search_paths:
    if os.path.isdir(os.path.join(path, "clipabit")):
        if path not in sys.path:
            sys.path.insert(0, path)
        package_found = True
        break

if not package_found:
    # Fallback: add current dir anyway so we can error out properly later
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

try:
    from clipabit.ui.main_window import main
except ImportError as e:
    # Fallback error reporting if package import fails
    print(f"Error importing ClipABit package: {e}")
    import traceback
    traceback.print_exc()
    
    # Try to show a dialog using basic PyQt if available (for user visibility)
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if not app:
            app = QApplication(sys.argv)
        QMessageBox.critical(None, "Import Error", f"Failed to load ClipABit plugin:\n{e}")
    except:
        pass
    sys.exit(1)

if __name__ == "__main__":
    # Resolve's 'resolve' global object is available in this top-level script scope
    # We pass it to the package to ensure it has access to the API
    try:
        # Check if 'resolve' exists in local or global scope
        resolve_obj = locals().get('resolve') or globals().get('resolve')
        
        # If not found, try 'app.GetResolve()' which works in some consoles
        if not resolve_obj:
            try:
                resolve_obj = app.GetResolve()
            except NameError:
                resolve_obj = None

        main(resolve_obj)
    except Exception as e:
        print(f"Critical Error running plugin: {e}")
        import traceback
        traceback.print_exc()