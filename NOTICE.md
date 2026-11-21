# Third-party notices

## AirPort Express tools

`vendor/airportctl/{__init__,acp,cfl}.py` and `vendor/ufs1.py` are copied from
[TDSOJohn/airport-express-tools](https://github.com/TDSOJohn/airport-express-tools),
revision `e78750e2a4a88fa8cafd98ee7935ef3b7b4c9f11`. The upstream MIT licence is
preserved verbatim as `vendor/AIRPORT-TOOLS-LICENSE`, including its original
copyright placeholder. Upstream package metadata identifies Giovanni Basso.
Its ACP implementation credits [x56/airpyrt-tools](https://github.com/x56/airpyrt-tools),
Copyright (c) 2016 Vince Cali, MIT licence.

The firmware container processing and build approach also draw on that project's
documented format and tooling. This project's patch preparation and installation
logic add the narrowly pinned USB matcher, startup changes, per-device checks
and private backups.

## MQTT-C

`native/vendor/mqtt-c/` contains MQTT-C by Liam Bindle at revision
`102e59c14dd0e01770b357a462bd462c83de62e2`, with its MIT licence and modification
record in that directory. Optional floating-point response-time statistics are
disabled for this target; the bridge supplies a NetBSD platform interface.

## Hardware protocols and build inputs

The PL2303 initialisation transfer values were informed by the Linux PL2303
driver's HX sequence. The native transport implementation uses NetBSD USB ioctls.
The Bose packet definitions come from the CD20 Serial Control Rev. 4 document.

NetBSD development archives and an Apple firmware image are downloaded separately
by the user-facing preparation command, checked by hash and kept locally. Their
licences and ownership remain with their respective authors; this project's MIT
licence does not relicense those inputs. Apple firmware, NetBSD archives and
generated native binaries are not included in this repository.

The Home Assistant screenshot was supplied by the project's owner for this README.
