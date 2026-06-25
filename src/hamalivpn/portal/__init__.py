"""HamaliVpn reseller / partner portal.

A self-contained module that powers the standalone web portal (decoupled from
the Telegram bot): secret-key auth, reseller balances with a double-entry
ledger, atomic VPN-key issuance through Remnawave, and the admin panel.

Money is stored everywhere as an integer number of kopecks to avoid floating
point drift. Convert at the API boundary only.
"""
