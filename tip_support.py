"""Optional, local-only tip display. Never used by mining or coinbase code.

The project maintainer supplied and approved the static public recipient below.
A release must independently decode its bundled QR and verify the payload.
There is no environment, receipt, URL, settings, or network override.
"""
from dataclasses import dataclass
import base64
import binascii
import hashlib
import re
import struct
import tkinter as tk
from tkinter import ttk
from address import payout_script


@dataclass(frozen=True)
class ApprovedTip:
    address: str
    qr_png_base64: str
    qr_sha256: str
    verified_qr_payload: str

    @property
    def uri(self):
        return 'bitcoin:' + self.address

    def validate(self):
        payout_script(self.address, 'mainnet')
        if self.address != self.address.lower():
            raise ValueError('The approved tip address must be canonical lowercase.')
        if self.verified_qr_payload != self.uri:
            raise ValueError('Tip address and verified QR payload do not match.')
        if not isinstance(self.qr_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.qr_sha256):
            raise ValueError('Missing pinned QR digest.')
        if not isinstance(self.qr_png_base64, str) or len(self.qr_png_base64) > 180_000:
            raise ValueError('Invalid bundled QR image.')
        raw = base64.b64decode(self.qr_png_base64, validate=True)
        if hashlib.sha256(raw).hexdigest() != self.qr_sha256:
            raise ValueError('Bundled QR image changed.')
        if len(raw) < 33 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
            raise ValueError('The bundled QR must be a PNG.')
        width, height = struct.unpack('>II', raw[16:24])
        if not 64 <= width == height <= 256:
            raise ValueError('The bundled QR must be a square 64-256 pixel image.')
        return self


# Explicit developer-tip recipient; never derived from the miner payout.
APPROVED_TIP = ApprovedTip(
    address='bc1qu4xz46wfg0fap3hy27g0cjddvvqfrxfclwqdx4',
    qr_png_base64='iVBORw0KGgoAAAANSUhEUgAAAM0AAADNAQAAAAAzx8nEAAACHElEQVR4nO2YP27bMBjFf7QEUJ3osRt9kUjJsQLIsBIH6LEqOReRTlBqowDZr4MdoIvTLqKG5o18w3vg95c04g6mzT0Gvqj/gUIKgNFMLMl6SmqAg2JqhwepJes58VwB+Ku11DbOpoCXHSUM9tU1mM1iWn+jal+ceEuj9TllNK9lIwcycRq3SObQf//2q0GMiW1sgBdjHql98R7OlWthMGab2Aa6ocG2ZIrm4yB1wY7NMG1GaXrSDA/bt2E5rU8o1+DO3hhK3ijeh2k3GdMktzHmTBvwnMbJgMt9YcJCWnchhay3khRLN/toNHuq1LmBpNmr01FqqT1VgFiFFWbKj27am/0OXI5tr/10Ka17kDSDCbW3Fw69WmofS5c+KCFTrELt1YVMVAGvywqD3u2NfXX5wMMWOLmZYjGtu5AkUVLf7gUyrRIUNUSc+liFTOqubT1xUG4FKx37WHLoKd3MOjMlZzJmBlzuI6Ptp8eltD6h3LF3F5f7AhhsF54r+zP1TLklpglZL4UaqoC3XeouiqTGS+GgWLqZWOm4TqWEGttde1imCGC1xm1IUqi9pAaqcBBl6oLNYdyCudiuiOB7xvPOdoto/cOrbdzvIoCptlkPY7POq83N3jxSD/E9vJgFte7hjxSF2gM16bevD8o1g2sBq5D19nWNoNzKdHqS7ccN+Fi6xDY+/jdC1tvWXfcwdEndvszXF9wXdQe/AXI3aC5WkHpEAAAAAElFTkSuQmCC',
    qr_sha256='34ba5160fcc318ed6b5ba9474e533774845a37e9ad6b6cae9b78da25cf9ec3c8',
    verified_qr_payload='bitcoin:bc1qu4xz46wfg0fap3hy27g0cjddvvqfrxfclwqdx4',
)


def configured_tip():
    """Fail closed: bad or absent release configuration disables payment details."""
    if not isinstance(APPROVED_TIP, ApprovedTip):
        return None
    try:
        return APPROVED_TIP.validate()
    except (ValueError, TypeError, AttributeError, binascii.Error, struct.error):
        return None


def tip_eligible(receipt, allow_continue, notice='', historical=False):
    """A restored, known mainnet outcome only. Never infer network permanence."""
    count = receipt.get('node_confirmations_at_check')
    return bool((historical or (allow_continue and not notice)) and receipt.get('network') == 'mainnet'
                and receipt.get('accepted_by_node') is True
                and receipt.get('active_chain_at_check') is True
                and type(count) is int and count > 0)


class TipSection(ttk.Frame):
    """A small disclosure; no RPC, transaction creation, wallet launch, or deduction."""
    def __init__(self, parent, historical=False):
        super().__init__(parent)
        self.expanded = False
        self.recipient = configured_tip()
        self.qr_image = None
        self.copy_button = None
        self.address_widget = None
        self.toggle_button = None
        ttk.Label(self, text='If this zero-fee miner made your day, consider buying Ryan a coffee (optional).',
                  wraplength=580).pack(anchor='w')
        self.body = ttk.Frame(self, padding=(10, 8))
        self.message = tk.StringVar(value='Tip address not configured. No payment details are available.')
        if self.recipient is not None:
            try:
                self.qr_image = tk.PhotoImage(master=self, data=self.recipient.qr_png_base64, format='png')
            except tk.TclError:
                self.recipient = None
        if self.recipient is None:
            ttk.Label(self.body, textvariable=self.message, wraplength=560).pack(anchor='w')
            self.body.pack(fill='x')
        else:
            self.toggle_button = ttk.Button(self, text='+ Show tip details', command=self.toggle)
            self.toggle_button.pack(anchor='w',pady=(4,0))
            ttk.Label(self.body, text='Tips are separate. No tip is deducted from the miner\'s payout.',
                      wraplength=560).pack(anchor='w', pady=(0, 8))
            ttk.Label(self.body, image=self.qr_image).pack(anchor='w')
            ttk.Label(self.body, text='Voluntary tip address (Bitcoin mainnet)', wraplength=560).pack(anchor='w', pady=(8, 2))
            self.address_widget = tk.Text(self.body, height=2, width=54, wrap='char', font=('Consolas', 10))
            self.address_widget.insert('1.0', self.recipient.address)
            self.address_widget.configure(state='disabled')
            self.address_widget.pack(fill='x')
            self.copy_button = ttk.Button(self.body, text='Copy tip address', command=self.copy_address)
            self.copy_button.pack(anchor='w', pady=(6, 0))
            self.message.set('Verify the full address in your wallet before sending. You choose the amount.')
            ttk.Label(self.body, textvariable=self.message, wraplength=560).pack(anchor='w', pady=(6, 0))

    def toggle(self):
        if not self.winfo_exists() or self.toggle_button is None:
            return
        self.expanded = not self.expanded
        if self.expanded:
            self.body.pack(fill='x')
        else:
            self.body.pack_forget()
        self.toggle_button.configure(text=('-' if self.expanded else '+') + ' Show tip details')

    def copy_address(self):
        # Validation cannot authorize a new recipient: this exact object came from
        # the immutable release configuration, never from the reward/receipt.
        if self.recipient is None:
            return False
        try:
            if not self.winfo_exists():
                return False
            self.recipient.validate()
            self.clipboard_clear()
            self.clipboard_append(self.recipient.address)
            if self.clipboard_get() != self.recipient.address:
                self.message.set('Clipboard check failed. Do not send until you verify the full address.')
                return False
        except (tk.TclError, ValueError, TypeError, binascii.Error, struct.error):
            try:
                self.message.set('Could not copy the address. Select it manually and verify it in your wallet.')
            except tk.TclError:
                pass
            return False
        self.message.set('Address copied. Verify the full recipient address in your wallet before sending.')
        return True
