from app_paths import DEFAULT_DATADIR
"""ZeroFee Solo window and tray. Opens idle; Start is always a user action."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
from queue import Empty
import tkinter as tk
import threading
from tkinter import messagebox, ttk
from gui_runner import ADDRESS, ROOT, RunConfig, Runner
from advanced_settings import PACING, TuningSettings, load_settings, save_settings
from win_moment import WinMoment,continuation_allowed
from windows_tray import Tray
from win_history import SavedWins

SIMULATION = 'Simulation - controls only'
MAINNET = 'Mainnet - bounded test'
MINING = 'Mine Bitcoin'
LONG_SIMULATION = 'Simulation - longer session'
DURATIONS = {'20 minutes':1200, '15 minutes':900, '1 hour':3600, '4 hours':14400, '8 hours':28800,
             '24 hours':86400, 'Until Stop':None}

def duration_text(seconds):
    if seconds is None:return 'Until Stop'
    seconds=max(0,int(seconds))
    return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'

def rate_text(rate):
    if rate is None:return '-'
    return f'{rate/1_000_000:.2f} MH/s' if rate>=1_000_000 else f'{rate/1000:.2f} kH/s'

class App:
    def __init__(self, root, runner=None, tray_factory=Tray, settings_path=None, acknowledgment_path=None, require_acknowledgment=True):
        self.root, self.runner = root, runner or Runner()
        self.tray, self.quitting, self.last_tray = None, False, None
        self.awaiting_result = False
        self.closed = False
        self.poll_id = None
        self.tray_error = None
        self.detecting = False
        self.start_after_detection = False
        self.recovering = False
        self.seen_wins=set()
        self.win_moments=[]
        self.wins_history=None
        self.advanced_dialog=None
        self.risk_dialog=None
        self.acknowledgment_path=Path(acknowledgment_path) if acknowledgment_path is not None else ROOT / 'risk-acknowledgment.json'
        from risk_notice import is_acknowledged
        self.risk_ready=not require_acknowledgment or is_acknowledged(self.acknowledgment_path)
        self.settings_path=Path(settings_path) if settings_path is not None else ROOT / 'gpu-settings.json'
        self.tuning, self.tuning_notice=load_settings(self.settings_path)
        root.title('ZeroFee Solo')
        root.geometry('640x385')
        root.minsize(620,385)
        root.iconbitmap(default=str(ROOT / 'pickaxe.ico'))
        self.mode = tk.StringVar(value=MINING)
        self.status = tk.StringVar(value='Idle')
        self.detail = tk.StringVar(value='Nothing is running. Simulation tests controls without mining.')
        self.metrics = tk.StringVar(value='Hashes searched: -    Accepted blocks: -')
        self.runtime = tk.StringVar(value='Elapsed: -    Remaining: -    Average rate: -')
        self.last_gpu_value = None
        self.gpu_detail = tk.StringVar(value='GPU core readings appear while mining; hotspot is not monitored. Board watts are not whole-PC power.')
        self.run_for, self.custom_minutes = tk.StringVar(value='20 minutes'), tk.StringVar(value='20')
        self.work_info = tk.StringVar(value='Work target: -    Observed tip changes: 0')
        self.session_note = tk.StringVar()
        self.pacing = tk.StringVar(value=self.tuning.pacing)
        self.temperature_limit = tk.StringVar(value=str(self.tuning.temperature_limit_c))
        self.tuning_summary = tk.StringVar(value=self.tuning_text())
        self.seconds, self.hashes = tk.StringVar(value='10'), tk.StringVar(value='32768')
        self.address, self.datadir = tk.StringVar(value=ADDRESS), tk.StringVar(value=DEFAULT_DATADIR)
        self.cookie, self.pid = tk.StringVar(), tk.StringVar()
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill='both', expand=True)
        heading=ttk.Frame(frame); heading.pack(fill='x')
        ttk.Label(heading, text='ZeroFee Solo', font=('Segoe UI',20,'bold')).pack(side='left')
        self.advanced_button=ttk.Button(heading,text='⚙ Advanced tuning',command=self.open_tuning)
        self.advanced_button.pack(side='right')
        ttk.Label(frame, text='Solo mining • No pool fees').pack(anchor='w')
        ttk.Label(frame, text='Solo GPU lotto • Budget for electricity; expect no reward.').pack(anchor='w',pady=(0,12))
        ttk.Label(frame, textvariable=self.status, font=('Segoe UI',17,'bold')).pack(anchor='w')
        ttk.Label(frame, textvariable=self.detail, wraplength=560).pack(anchor='w',pady=(3,8))
        ttk.Label(frame, textvariable=self.metrics).pack(anchor='w',pady=(0,12))
        ttk.Label(frame, textvariable=self.runtime).pack(anchor='w',pady=(0,4))
        ttk.Label(frame, textvariable=self.work_info).pack(anchor='w',pady=(0,4))
        ttk.Label(frame, textvariable=self.gpu_detail,wraplength=580).pack(anchor='w',pady=(0,10))
        self.mode_box = ttk.Combobox(frame, textvariable=self.mode, values=[MINING,LONG_SIMULATION,MAINNET,SIMULATION], state='readonly')
        self.mode_box.pack(fill='x')
        self.mode_box.bind('<<ComboboxSelected>>', lambda event:self.mode_changed())
        self.mining_controls = mining = ttk.LabelFrame(frame,text='Mining session',padding=10)
        mining.columnconfigure(1,weight=1)
        ttk.Label(mining,text='Run for').grid(row=0,column=0,sticky='w',pady=4)
        self.duration_box = ttk.Combobox(mining,textvariable=self.run_for,
            values=list(DURATIONS)+['Custom minutes'],state='readonly')
        self.duration_box.grid(row=0,column=1,sticky='ew',padx=10,pady=4)
        self.duration_box.bind('<<ComboboxSelected>>',lambda event:self.refresh_controls())
        ttk.Label(mining,text='Custom minutes').grid(row=1,column=0,sticky='w',pady=4)
        self.custom_entry = ttk.Entry(mining,textvariable=self.custom_minutes)
        self.custom_entry.grid(row=1,column=1,sticky='ew',padx=10,pady=4)
        # Legacy variables remain available to config/tests; visible tuning lives in the modal.
        hidden_tuning=ttk.Frame(mining)
        self.pacing_box=ttk.Combobox(hidden_tuning,textvariable=self.pacing,values=list(PACING),state='readonly')
        self.temperature_entry=ttk.Spinbox(hidden_tuning,from_=60,to=90,textvariable=self.temperature_limit,width=8)
        ttk.Label(mining,text='Public reward address').grid(row=2,column=0,sticky='w',pady=4)
        self.reward_entry = ttk.Entry(mining,textvariable=self.address)
        self.reward_entry.grid(row=2,column=1,sticky='ew',padx=10,pady=4)
        ttk.Label(mining,textvariable=self.tuning_summary,wraplength=580).grid(row=3,column=0,columnspan=2,sticky='w',pady=(8,0))
        ttk.Label(mining,textvariable=self.session_note,wraplength=580).grid(row=4,column=0,columnspan=2,sticky='w',pady=(8,0))
        self.bounds = bounds = ttk.Frame(frame)
        bounds.pack(fill='x',pady=10)
        ttk.Label(bounds,text='Time limit (s)').grid(row=0,column=0,sticky='w')
        ttk.Label(bounds,text='Hash limit').grid(row=0,column=2,padx=(20,0),sticky='w')
        self.bound_entries = [ttk.Entry(bounds,textvariable=self.seconds,width=8), ttk.Entry(bounds,textvariable=self.hashes,width=14)]
        self.bound_entries[0].grid(row=0,column=1,padx=8)
        self.bound_entries[1].grid(row=0,column=3,padx=8)
        self.session = session = ttk.LabelFrame(frame, text='Mainnet connection - fresh approval for each run',padding=10)
        session.pack(fill='x')
        session.columnconfigure(1,weight=1)
        self.session_entries = []
        for row,(label,var) in enumerate([('Public reward address',self.address),('Core data folder',self.datadir),
                                          ('Private cookie path',self.cookie),('Core process ID',self.pid)]):
            ttk.Label(session,text=label).grid(row=row,column=0,sticky='w',pady=3)
            entry = ttk.Entry(session,textvariable=var)
            entry.grid(row=row,column=1,sticky='ew',padx=(10,0),pady=3)
            self.session_entries.append(entry)
        self.detect_button = ttk.Button(session, text='Detect Core connection', command=self.detect_core)
        self.detect_button.grid(row=4,column=1,sticky='w',padx=(10,0),pady=(6,0))
        actions = ttk.Frame(session)
        actions.grid(row=5,column=0,columnspan=2,sticky='ew',pady=(10,0))
        self.connect_button = ttk.Button(actions, text='Connect & run once', command=self.connect_run)
        self.connect_button.pack(side='left')
        self.restore_button = ttk.Button(actions, text='Restore normal Core', command=self.restore_core)
        self.restore_button.pack(side='left',padx=10)
        self.mainnet_note = ttk.Label(frame,text='Connect & run once handles temporary access and normal Core restoration.\nStart uses an existing session. Both stop at the first limit or one accepted block.\nStartup is outside the timer. No automatic mining after opening or reboot.',wraplength=580)
        self.mainnet_note.pack(anchor='w',pady=10)
        self.buttons = buttons = ttk.Frame(frame)
        buttons.pack(fill='x',pady=5)
        self.start_button = ttk.Button(buttons,text='Start',command=self.start)
        self.stop_button = ttk.Button(buttons,text='Stop',command=self.stop)
        self.hide_button = ttk.Button(buttons,text='Hide to tray',command=self.hide)
        self.wins_button = ttk.Button(buttons,text='View wins',command=self.view_wins)
        self.start_button.pack(side='left')
        self.stop_button.pack(side='left',padx=8)
        self.wins_button.pack(side='left')
        self.hide_button.pack(side='right')
        ttk.Button(buttons,text='Exit',command=self.exit).pack(side='right',padx=8)
        ttk.Label(frame,text='× hides to the pickaxe tray icon. Tray Exit stops safely, then closes.',wraplength=560).pack(anchor='w',pady=(10,0))
        root.protocol('WM_DELETE_WINDOW',self.hide)
        try:
            self.tray = tray_factory(ROOT / 'pickaxe.ico', lambda action:self.runner.events.put(('action',action)))
        except Exception as exc:
            self.tray_error = 'Tray unavailable. The window will stay accessible. ' + str(exc)
        self.mode_changed()
        if self.connection_pending():
            self.status.set('Core connection needs review')
            self.detail.set('A previous connection was interrupted. Select Mainnet, then Restore normal Core. Mining will not resume automatically.')
        if self.tuning_notice:
            self.detail.set(self.tuning_notice)
        self.refresh_controls()
        self.poll_id = root.after(150,self.poll)
        if not self.risk_ready:
            from risk_notice import RiskAcknowledgment
            self.risk_dialog=RiskAcknowledgment(self)

    def tuning_text(self):
        preset=self.tuning.pacing.split(' - ')[0]
        return f'{preset}: {self.tuning.pause_seconds*1000:g} ms pause · {self.tuning.batch_size:,} hashes · {self.tuning.threads_per_block} threads · core stop {self.tuning.temperature_limit_c:g}°C'

    def open_tuning(self):
        if self.advanced_dialog is not None:
            self.advanced_dialog.window.lift(); return
        if self.runner.busy or self.awaiting_result or self.detecting or self.quitting or self.closed or self.advanced_dialog is not None or not self.risk_ready:
            return
        from advanced_tuning import AdvancedTuning
        self.advanced_dialog=AdvancedTuning(self)
        self.refresh_controls()

    def save_tuning(self, settings):
        if self.runner.busy or self.awaiting_result or self.detecting or self.quitting or self.closed:
            raise RuntimeError('Wait until the session has stopped before saving GPU settings.')
        save_settings(self.settings_path, settings)
        self.tuning=settings
        self.pacing.set(settings.pacing)
        self.temperature_limit.set(str(settings.temperature_limit_c))
        self.tuning_summary.set(self.tuning_text())
        self.tuning_notice=''

    def view_wins(self):
        if self.closed:
            return None
        if self.wins_history is None or self.wins_history.closed:
            self.wins_history=SavedWins(self.root,ROOT)
        else:
            self.wins_history.refresh();self.wins_history.show()
        return self.wins_history

    def mode_changed(self):
        long_mode = self.mode.get() in (MINING,LONG_SIMULATION)
        self.session_note.set('Controls simulation only: no GPU, RPC, hashes or Core changes.\nStart/Stop follow the selected duration. No mining results are simulated.' if self.mode.get()==LONG_SIMULATION else
            'Start connects Core and mines for the selected duration, with no hash cap.\nStop ends mining and restores Core. One accepted block also stops the session.\nCore wallets are closed while mining. Pacing adds idle time; it is not a power cap.')
        self.mining_controls.pack_forget()
        self.bounds.pack_forget()
        if long_mode:
            self.mining_controls.pack(fill='x',pady=10,before=self.buttons)
        else:
            self.bounds.pack(fill='x',pady=10,before=self.buttons)
        if self.mode.get()==MAINNET:
            self.session.pack(fill='x',before=self.buttons)
            self.mainnet_note.pack(anchor='w',pady=10,before=self.buttons)
            self.root.geometry('680x790')
            self.root.minsize(660,790)
        else:
            self.session.pack_forget()
            self.mainnet_note.pack_forget()
            self.root.minsize(660,670 if long_mode else 485)
            self.root.geometry('680x670' if long_mode else '680x485')
        if not self.runner.busy:
            if not self.awaiting_result:
                self.status.set('Idle')
            self.detail.set('Choose a duration and press Start. Stop ends mining and restores normal Core.' if self.mode.get()==MINING else
                'Simulates the selected duration and Start/Stop controls. No GPU, RPC or hashes.' if self.mode.get()==LONG_SIMULATION else
                'Simulation tests Start/Stop only. No GPU, node or hashes.' if self.mode.get()==SIMULATION else
                'Bounded test: choose time/hash caps, then Start. Existing-session fields are optional.')
            if self.connection_pending():
                self.status.set('Core connection needs review')
                self.detail.set('A previous connection was interrupted. Select Mainnet, then Restore normal Core. No run resumes automatically.')
        if self.tray_error:
            self.detail.set(self.tray_error)
        self.refresh_controls()

    def refresh_controls(self):
        active = self.runner.busy or self.awaiting_result
        locked = active or self.detecting or self.advanced_dialog is not None or not self.risk_ready
        self.advanced_button.configure(state='disabled' if locked or self.quitting else 'normal')
        if self.advanced_dialog is not None:
            self.advanced_dialog.refresh()
        self.start_button.configure(state='disabled' if locked or self.quitting else 'normal')
        self.stop_button.configure(state='normal' if active and not self.runner.stop_event.is_set() else 'disabled')
        self.mode_box.configure(state='disabled' if locked else 'readonly')
        self.duration_box.configure(state='disabled' if locked else 'readonly')
        self.pacing_box.configure(state='disabled' if locked else 'readonly')
        self.custom_entry.configure(state='normal' if not locked and self.run_for.get()=='Custom minutes' else 'disabled')
        self.reward_entry.configure(state='disabled' if locked else 'normal')
        self.temperature_entry.configure(state='disabled' if locked else 'normal')
        for entry in self.bound_entries:
            entry.configure(state='disabled' if locked else 'normal')
        for entry in self.session_entries:
            entry.configure(state='normal' if not locked and self.mode.get()==MAINNET else 'disabled')
        self.detect_button.configure(state='disabled' if locked or self.quitting else 'normal')
        self.connect_button.configure(state='disabled' if locked or self.quitting or self.connection_pending() else 'normal')
        self.restore_button.configure(state='normal' if not locked and not self.quitting and self.connection_pending() else 'disabled')
        self.hide_button.configure(state='normal' if self.tray and self.tray.present else 'disabled')
        state = (self.status.get(),locked or self.quitting)
        if self.tray and self.last_tray != state:
            self.tray.update(*state)
            self.last_tray = state

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        if self.tray and self.tray.present:
            self.root.withdraw()
        else:
            self.show()
            if messagebox.askyesno('Tray unavailable','The tray icon is unavailable. Exit the app instead? Any active run will stop safely.',parent=self.root):
                self.exit()

    def start(self):
        if self.runner.busy or self.awaiting_result or self.detecting or self.quitting or self.closed or self.advanced_dialog is not None or not self.risk_ready:
            return
        if self.mode.get() in (MAINNET,MINING) and self.connection_pending():
            self.status.set('Core connection needs review')
            self.detail.set('Restore/review the previous connection before another mainnet run.')
            return
        if self.mode.get()==MINING:
            self.connect_run()
            return
        if self.mode.get()==MAINNET and (not self.cookie.get().strip() or not self.pid.get().strip()):
            self.detect_core(start_when_ready=True)
            return
        self.show()
        try:
            config = self.chosen_config()
            config.validate()
            approved = False
            if config.mode=='mainnet':
                approved = messagebox.askyesno('Approve this bounded mainnet run',
                    f'Search up to {config.hashes:,} hashes or {config.seconds:g} mining-loop seconds, stopping at one accepted block.\n\n'
                    f'Reward address:\n{config.address}\n\nSubmit a valid block if found? This uses the existing temporary session. '
                    'Startup is outside the timer; electricity costs apply. You must restore normal Core after the session.\n\n'
                    'Approve this single run and block submission?',default='no',parent=self.root)
                if not approved:
                    return
            self.metrics.set('Hashes searched: -    Accepted blocks: -')
            self.status.set('Starting')
            self.detail.set('Simulation only - no mining.' if config.mode=='simulation' else 'Checking session and work before any GPU search.')
            self.runner.start(config,approved)
            self.awaiting_result = True
        except Exception as exc:
            self.status.set('Could not start')
            self.detail.set(str(exc))
        self.refresh_controls()

    def chosen_config(self, managed=False, force_mainnet=False):
        long_mode = self.mode.get() in (MINING,LONG_SIMULATION)
        seconds = (float(self.custom_minutes.get())*60 if self.run_for.get()=='Custom minutes'
                   else DURATIONS[self.run_for.get()]) if long_mode else float(self.seconds.get())
        tuning=TuningSettings(pacing=self.pacing.get(),custom_pause_ms=self.tuning.custom_pause_ms,
            batch_size=self.tuning.batch_size,threads_per_block=self.tuning.threads_per_block,
            temperature_limit_c=float(self.temperature_limit.get())).validate()
        return RunConfig(mode='mainnet' if force_mainnet or self.mode.get() in (MINING,MAINNET) else 'simulation',
            seconds=seconds,hashes=None if long_mode else int(self.hashes.get()),
            profile='session' if long_mode else 'test',pause_seconds=tuning.pause_seconds if long_mode else 0,
            address=self.address.get().strip(),datadir=self.datadir.get().strip(),
            cookie=self.cookie.get().strip(),core_pid=int(self.pid.get() or '0'),managed=managed,
            temperature_limit_c=tuning.temperature_limit_c,batch_size=tuning.batch_size,
            threads_per_block=tuning.threads_per_block)

    def scope_text(self, config):
        if config.profile=='session':
            return (f'Run for {duration_text(config.seconds)} with no hash cap, stopping at one accepted block or when you press Stop.\n'
                    f'Pause {config.pause_seconds*1000:g} ms between {config.batch_size:,}-hash batches ({config.threads_per_block} threads per block). Electricity and GPU heat apply.\n'
                    f'GPU core stop: {config.temperature_limit_c:g}°C; stop after {config.telemetry_grace_seconds:g} seconds of missing readings. Hotspot is not monitored.\n')
        return f'Search at most {config.hashes:,} hashes or {config.seconds:g} mining-loop seconds, stopping at one accepted block.\n'

    def connection_pending(self):
        return any((ROOT / name).exists() for name in ('core-connection.json', 'core-connection.json.tmp'))

    def connect_run(self):
        if self.runner.busy or self.awaiting_result or self.detecting or self.quitting or self.closed or self.advanced_dialog is not None or not self.risk_ready:
            return
        self.show()
        try:
            if self.connection_pending():
                raise ValueError('Restore/review the previous connection before another run.')
            config = self.chosen_config(managed=True,force_mainnet=True)
            config.validate()
            approved = messagebox.askyesno('Start Bitcoin mining' if config.profile=='session' else 'Approve temporary connection and one bounded run',
                f'Reward address (confirm it is yours):\n{config.address}\n\n'
                + self.scope_text(config) +
                'Submit a valid block if found. Startup is outside the timer; electricity costs apply.\n\n'
                'This will cleanly close normal Bitcoin Core if open, create a new protected temporary cookie, '
                'and start the installed Core with wallets disabled and authenticated localhost-only RPC on port 19732. '
                'Only chain/template/address queries and valid-block submission are allowed.\n\n'
                'Longer runs keep the temporary cookie in miner memory for reusable local RPC and a separate read-only block watcher. It is never logged.\n\n'
                'Core wallets will be unavailable for the entire selected session. The temporary local connection lasts until it ends or you Stop.\n\n'
                'After Stop, completion, or error, close temporary Core and reopen normal Core with its wallets. '
                'If Core minimizes instead of closing, use Core File > Exit, then Restore normal Core. '
                'An uncertain submission keeps this connection available for review and blocks another run.\n\n'
                'Core configuration, firewall, passwords, startup and GPU settings stay unchanged. '
                'No private key or seed is needed. Approve this single connection, run, submission, and restoration?',
                default='no', parent=self.root)
            if not approved:
                return
            self.metrics.set('Hashes searched: -    Accepted blocks: -')
            self.status.set('Connecting Core')
            self.detail.set('Preparing one approved temporary session. Stop cancels mining and waits for safe Core restoration.')
            self.runner.start(config, approved=True, connection_approved=True)
            self.awaiting_result = True
        except Exception as exc:
            self.status.set('Could not connect')
            self.detail.set(str(exc))
        self.refresh_controls()

    def restore_core(self):
        if self.runner.busy or self.awaiting_result or self.detecting or self.quitting or self.closed or self.advanced_dialog is not None or not self.risk_ready:
            return
        self.show()
        if not messagebox.askyesno('Restore normal Bitcoin Core',
            'Check the saved connection and current Core identity, close only its matching temporary Core, '
            'and open normal Core with the default data folder and wallets enabled. Remove only the empty session directory.\n\n'
            'No mining or block retry will occur. Unresolved submissions, changed configuration, or unexpected processes require review. '
            'Approve this restoration?', default='no', parent=self.root):
            return
        try:
            self.runner.restore_core(approved=True)
            self.awaiting_result = True
            self.recovering = True
            self.status.set('Restoring normal Core')
            self.detail.set('Waiting for safe restoration. This does not retry a mining run or submission.')
        except Exception as exc:
            self.detail.set(str(exc))
        self.refresh_controls()

    def stop(self):
        if self.recovering:
            self.detail.set('Restoration must finish safely before exit; no force-kill is used.')
            return
        self.runner.stop()
        if self.runner.busy:
            self.status.set('Stopping')
            self.detail.set('Waiting for the current check, GPU batch or submission to finish safely. No force-kill.')
        self.refresh_controls()

    def detect_core(self, start_when_ready=False):
        if self.runner.busy or self.awaiting_result or self.detecting or self.quitting or self.closed or self.advanced_dialog is not None or not self.risk_ready:
            return
        self.detecting = True
        self.start_after_detection = start_when_ready
        datadir = self.datadir.get().strip()
        self.status.set('Checking Core')
        self.detail.set('Looking for an existing private mining connection. No credentials are displayed or saved.')
        def check():
            try:
                from core_session import discover_session
                session = discover_session(datadir)
                value = (session, None)
            except Exception as exc:
                value = (None, str(exc))
            self.runner.events.put(('session', value))
        try:
            threading.Thread(target=check, name='CoreConnectionCheck', daemon=True).start()
        except Exception as exc:
            self.detecting = False
            self.start_after_detection = False
            self.status.set('Connection check failed')
            self.detail.set(str(exc))
        self.refresh_controls()

    def exit(self):
        self.quitting = True
        self.stop()
        if self.runner.busy or self.awaiting_result:
            self.show()
            self.detail.set('Stopping safely before exit. Keep this app open until the current operation finishes.')
        else:
            self.finish_exit()

    def finish_exit(self):
        if self.closed:
            return
        self.closed = True
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        if self.tray:
            self.tray.close()
        self.root.destroy()

    def poll(self):
        if self.closed:
            return
        self.poll_id = None
        try:
            while True:
                kind,value = self.runner.events.get_nowait()
                if kind=='action':
                    if value=='tray_failed':
                        self.show()
                        self.detail.set('Tray icon unavailable. Use this window; closing will offer safe exit.')
                    else:
                        {'open':self.show,'start':self.start,'stop':self.stop,'exit':self.exit}[value]()
                        if self.closed:
                            return
                elif kind=='session':
                    self.detecting = False
                    resume = self.start_after_detection
                    self.start_after_detection = False
                    session, error = value
                    if error:
                        self.cookie.set(''); self.pid.set('')
                        self.status.set('Core connection unavailable')
                        self.detail.set(error)
                        if resume and not self.quitting:
                            self.connect_run()  # The explicit connection/run prompt still defaults to No.
                    else:
                        self.cookie.set(session['cookie']); self.pid.set(str(session['core_pid']))
                        self.status.set('Core connection found')
                        self.detail.set('Private Core session found. No mining has started.')
                        if resume and not self.quitting:
                            self.start()
                elif kind=='status':
                    self.status.set('Stopping' if self.runner.stop_event.is_set() and self.runner.busy and 'Core' not in value else
                                    'Finishing' if value=='Stopped' else value)
                    if value=='Mining':self.detail.set('Mining fresh Core work. Stop ends this run and restores normal Core.')
                elif kind=='benchmark_progress':
                    if self.advanced_dialog is not None:self.advanced_dialog.progress(value)
                elif kind=='progress':
                    if value['hashes'] is not None:
                        self.metrics.set(f"Hashes searched: {value['hashes']:,}    Accepted blocks: {value['blocks']}")
                    if 'elapsed_seconds' in value:
                        self.runtime.set(f"Elapsed: {duration_text(value['elapsed_seconds'])}    Remaining: {duration_text(value['remaining_seconds'])}    Average: {rate_text(value['hashes_per_second'])}")
                    if value.get('work_height') is not None:
                        self.work_info.set(f"Mining height: {value['work_height']}    Observed tip changes: {value.get('tip_changes',0)}")
                elif kind=='work':
                    self.work_info.set(f"Mining height: {value['height']}    Observed tip changes: {value['tip_changes']}")
                elif kind=='gpu':
                    from gpu_monitor import reading_text
                    self.last_gpu_value = value
                    self.gpu_detail.set(reading_text(value))
                elif kind=='safety_stop':
                    self.status.set('Stopping — GPU safety')
                    self.detail.set(value)
                elif kind=='notice':
                    self.detail.set(value)
                elif kind=='finished':
                    self.awaiting_result = False
                    self.recovering = False
                    result = value['result']
                    if result and result.get('hashes') is not None:
                        self.metrics.set(f"Hashes searched: {result['hashes']:,}    Accepted blocks: {len(result['blocks'])}")
                    if value.get('restoration_error'):
                        self.status.set('Stopped - Core needs attention')
                        self.detail.set((value.get('error') or '') + ' ' + value['restoration_error'] + ' Use Restore normal Core after reviewing the issue. No automatic retry.')
                        self.quitting = False
                        self.show()
                    elif value['mode']=='benchmark':
                        self.status.set('Benchmark stopped' if value.get('error') or (value.get('benchmark') or {}).get('status')!='COMPLETE' else 'Benchmark complete')
                        self.detail.set(value.get('error') or 'Synthetic GPU benchmark ended. Results are separate from mining; settings were not changed.')
                        if self.advanced_dialog is not None:self.advanced_dialog.finished(value)
                    elif value['mode']=='recovery':
                        self.status.set('Core restored' if not value['error'] else 'Core restoration needs review')
                        self.detail.set(value['error'] or 'Normal Core process and arguments verified; temporary access is removed. Check wallet loading and peer sync in Core.')
                        self.cookie.set(''); self.pid.set('')
                    elif value['error']:
                        self.status.set('Error - worker stopped')
                        self.detail.set(value['error'])
                    elif value.get('stop_reason'):
                        self.status.set('Stopped — GPU safety')
                        self.detail.set(value['stop_reason'])
                    elif result and result.get('submission_uncertain'):
                        self.status.set('Stopped - submission needs review')
                        self.detail.set(result['submission_uncertain'] + ' Do not retry until its outcome is resolved.')
                    elif value['mode']=='simulation':
                        self.status.set('Stopped - simulation')
                        self.detail.set('Controls test ended. No GPU, RPC, hashes or block submissions.')
                    else:
                        self.status.set('Stopped')
                        self.detail.set('Run ended and worker resources closed.' if value.get('restoration') else
                            'Run ended and worker resources closed. Temporary Core access still needs separate restoration.')
                        if result and result.get('hashes') is not None:
                            self.metrics.set(f"Hashes searched: {result['hashes']:,}    Accepted blocks: {len(result['blocks'])}")
                    if value.get('restoration') and value['mode']!='recovery':
                        self.cookie.set(''); self.pid.set('')
                        self.detail.set(self.detail.get() + ' Normal Core process restored; temporary access removed. Check wallet loading and peer sync in Core.')
                    if value['mode']!='benchmark' and not self.quitting and not (result or {}).get('submission_uncertain'):
                        for receipt in value.get('win_receipts') or []:
                            key=(receipt['network'],receipt['block_hash'])
                            if key in self.seen_wins:continue
                            self.seen_wins.add(key)
                            self.show()
                            if not value.get('error') and not value.get('restoration_error') and not value.get('stop_reason'):
                                self.status.set('Block accepted by your node!')
                                self.detail.set('Receipt needs attention; copy the block details before another run.' if value.get('win_receipt_error') else
                                    'Win receipt saved. Review the chain snapshot and choose Continue mining or Finish for now.')
                            self.win_moments.append(WinMoment(self.root,receipt,on_continue=self.start,
                                allow_continue=continuation_allowed(value),notice=value.get('restoration_error') or value.get('stop_reason') or value.get('error') or
                                ('Receipt was not saved. Copy these details before continuing.' if value.get('win_receipt_error') else '')))
                    if value.get('win_receipt_error'):
                        self.detail.set(self.detail.get()+' Win receipt could not be saved: '+value['win_receipt_error'])
                    if self.quitting:
                        self.finish_exit()
                        return
        except Empty:
            pass
        self.refresh_controls()
        if isinstance(self.last_gpu_value, dict):
            from gpu_monitor import reading_text
            self.gpu_detail.set(reading_text(self.last_gpu_value))
        self.poll_id = self.root.after(150,self.poll)

def main():
    # A second click opens the existing window; it never creates a second worker.
    kernel = C.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes = [C.c_void_p,W.BOOL,W.LPCWSTR]
    kernel.CreateMutexW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    mutex = kernel.CreateMutexW(None,False,'Local\\ZeroFeeSoloGUI')
    if not mutex:
        raise C.WinError(C.get_last_error())
    try:
        if C.get_last_error()==183:
            user = C.WinDLL('user32',use_last_error=True)
            user.FindWindowW.argtypes = [W.LPCWSTR,W.LPCWSTR]
            user.FindWindowW.restype = W.HWND
            user.ShowWindow.argtypes = [W.HWND,C.c_int]
            user.SetForegroundWindow.argtypes = [W.HWND]
            window = user.FindWindowW(None,'ZeroFee Solo')
            if window:
                user.ShowWindow(window,9)
                user.SetForegroundWindow(window)
            return
        root = tk.Tk()
        App(root)
        root.mainloop()
    finally:
        kernel.CloseHandle(mutex)

if __name__=='__main__':
    main()
