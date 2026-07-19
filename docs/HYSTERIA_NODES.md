# Hysteria2 nodes

The LTE nodes use HTTP authentication against the control API. Their server
configuration must point to the stable public endpoint:

```text
https://app.hamali.ru/hysteria/auth
```

Do not use a control-server IP in the node configuration. A server migration
would otherwise leave Hysteria listening on UDP while every login fails.

## Safe update procedure

1. Confirm key-based SSH access to the target node.
2. Back up `/etc/hysteria/config.yaml` on that node.
3. Replace only `auth.http.url`; keep TLS keys, certificate, obfs password and
   listen port unchanged.
4. Validate the configuration with the installed Hysteria binary.
5. Restart one node and run an end-to-end SOCKS5 request through it.
6. Only after that succeeds, repeat for the second node.

The reference structure is in `infra/hysteria/server.yaml.example`. Secrets
belong in the node's root-owned configuration and must never be committed.

## Client TLS

The nodes use self-signed certificates. Client links therefore include both
`insecure=1` and `pinSHA256`: public-CA validation is disabled, but the pinned
leaf certificate still prevents an untrusted certificate from being accepted.
