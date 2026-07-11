# Elexon fixtures

These small, sanitized responses preserve the public field names and realistic
values observed from the Elexon Insights Solution API on 11 July 2026. They contain
no customer or personal data.

- `fuelinst.json`: `GET /datasets/FUELINST?format=json`
- `indo.json`: `GET /datasets/INDO?format=json`
- `frequency_stream.json`: `GET /datasets/FREQ/stream`
- `interconnectors_historic.json`: the compatible half-hour response from
  `GET /generation/outturn/interconnectors?format=json`

The production interconnector adapter filters five-minute `INT*` records from raw
FUELINST so the Live map does not inherit the half-hour endpoint's additional lag.
