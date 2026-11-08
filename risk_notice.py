"""Local, versioned first-launch risk acknowledgment; accepting never starts work."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk

NOTICE_VERSION = 1
RISK_TEXT = (
    'Use this software and Advanced GPU Tuning at your own risk. Mining and synthetic benchmarks can '
    'stress your GPU for sustained periods, consume electricity, generate heat, cause instability, '
    'and contribute to hardware wear or damage. Not all pacing presets push maximum load; '
    'a pause is not a power cap.\n\n'
    'Temperature cutoffs and missing-reading stops are best-effort cooperative safeguards. '
    'They cannot guarantee protection against hardware damage, sensor failure, overheating or crashes. '
    'GPU hotspot temperature is not monitored. Stop may wait for the current GPU call.\n\n'
    'This software is provided as-is, without warranty. Solo Bitcoin mining has extremely low odds '
    'of finding a block; electricity costs can greatly exceed any reward. Expect no reward. '
    'A short benchmark is only a starting point, not a sustained, thermally soaked result.\n\n'
    'This acknowledgment records that you read this notice. It does not eliminate liability or '
    'waive applicable rights. Acknowledging does not start mining or a benchmark.'
)

def is_acknowledged(path):
    try:
        path=Path(path)
        if path.stat().st_size > 4096:return False
        value=json.loads(path.read_text(encoding='utf-8'))
        return type(value) is dict and type(value.get('notice_version')) is int and value['notice_version']==NOTICE_VERSION and value.get('acknowledged') is True
    except (OSError, ValueError, TypeError):
        return False

def record_acknowledgment(path):
    path=Path(path);temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(notice_version=NOTICE_VERSION,acknowledged=True,
        recorded_utc=datetime.now(timezone.utc).isoformat()),indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)

class RiskAcknowledgment:
    def __init__(self,app):
        self.app=app
        self.window=window=tk.Toplevel(app.root)
        window.title('Before using ZeroFee Solo')
        window.transient(app.root);window.resizable(False,False)
        frame=ttk.Frame(window,padding=24);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Use at your own risk',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(frame,text=RISK_TEXT,wraplength=550,justify='left').pack(anchor='w',pady=14)
        self.checked=tk.BooleanVar(window,value=False)
        ttk.Checkbutton(frame,text='I have read and understand this risk notice.',variable=self.checked,command=self.refresh).pack(anchor='w')
        self.error=tk.StringVar(window)
        ttk.Label(frame,textvariable=self.error,foreground='#a02020',wraplength=550).pack(anchor='w',pady=8)
        buttons=ttk.Frame(frame);buttons.pack(fill='x')
        ttk.Button(buttons,text='Exit',command=app.exit).pack(side='left')
        self.accept_button=ttk.Button(buttons,text='Acknowledge & open app',command=self.accept,state='disabled')
        self.accept_button.pack(side='right')
        window.protocol('WM_DELETE_WINDOW',app.exit)
        window.grab_set()

    def refresh(self):
        self.accept_button.configure(state='normal' if self.checked.get() else 'disabled')

    def accept(self):
        if not self.checked.get():return
        try:record_acknowledgment(self.app.acknowledgment_path)
        except OSError as exc:
            self.error.set('Could not save your acknowledgment: '+str(exc));return
        self.app.risk_ready=True
        self.window.grab_release();self.window.destroy();self.app.risk_dialog=None
        self.app.refresh_controls()
