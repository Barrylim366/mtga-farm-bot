import tkinter as tk
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui

root = tk.Tk()
root.geometry("1x1+3000+3000")
root.update()

class FakeCM:
    def get_account_switch_minutes(self): return 30
    def get_managed_accounts(self): return [
        {'name': 'AccountOne', 'email': '***REMOVED***', 'pw': 'secret', 'folder': ''},
        {'name': 'AccountTwo', 'email': '***REMOVED***', 'pw': 'pass', 'folder': ''},
        {'name': 'AccountThree', 'email': '***REMOVED***', 'pw': 'pass', 'folder': ''},
    ]
    def get_account_play_order(self): return ['AccountThree', 'AccountTwo', 'AccountOne', '', '', '', '', '', '', '']
    def save_managed_accounts(self, a): return a
    def set_account_switch_minutes(self, v): pass
    def set_account_play_order(self, o): pass
    def set_account_cycle_index(self, i): pass

frame = tk.Frame(root)
frame.pack()
frame.master = root

w = ui.SwitchAccountWindow(frame, config_manager=FakeCM())
root.update()
w.update()

# Force position top-left so the full window is on screen
w.geometry("+50+30")
w.attributes('-topmost', True)
w.lift()
w.focus_force()

# Let it render fully
for _ in range(20):
    root.update()
    time.sleep(0.1)

w.update_idletasks()
root.update()

x = w.winfo_x()
y = w.winfo_y()
width = w.winfo_width()
height = w.winfo_height()
print(f'Window: ({x},{y}) {width}x{height}')

out = r'C:\Users\giaco\AppData\Local\Temp\manage_accounts_new.png'
from PIL import ImageGrab
img = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
img.save(out)
print('Saved:', out)

w.destroy()
root.destroy()
