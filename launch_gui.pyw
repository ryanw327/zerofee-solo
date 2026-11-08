"""Local window launcher. No mining starts until the user selects Start."""
try:
    from miner_gui import main
    main()
except Exception as exc:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror('ZeroFee Solo could not open', str(exc), parent=root)
    root.destroy()
