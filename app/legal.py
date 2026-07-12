"""Public, versioned release pages required by the native app stores."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(include_in_schema=False)


def _page(*, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{title} · 50Hz</title>
  <style>
    :root {{ color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #080b0d; color: #e9f1f3; line-height: 1.58; }}
    main {{ width: min(700px, calc(100% - 40px)); margin: 0 auto; padding: 64px 0 88px; }}
    h1 {{ font-size: clamp(2rem, 7vw, 3.4rem); letter-spacing: -.04em; line-height: 1; margin: 0 0 12px; }}
    h2 {{ font-size: 1.05rem; margin: 32px 0 6px; color: #8eeafb; }}
    p, li {{ color: #bac7cb; }}
    a {{ color: #8eeafb; }}
    .eyebrow {{ color: #8eeafb; font: 600 .72rem ui-monospace, monospace; letter-spacing: .13em; text-transform: uppercase; }}
    .updated {{ color: #76868b; font: .78rem ui-monospace, monospace; }}
    footer {{ margin-top: 44px; padding-top: 18px; border-top: 1px solid #263036; font-size: .85rem; }}
  </style>
</head>
<body><main><div class="eyebrow">Britain’s electricity system, alive</div>{body}
<footer><a href="/privacy">Privacy</a> · <a href="/support">Support</a> · <a href="https://github.com/papajohn-123/50hz">Source code</a></footer>
</main></body></html>""".format(title=title, body=body)
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy() -> HTMLResponse:
    return _page(
        title="Privacy policy",
        body="""
<h1>Privacy policy</h1>
<p class="updated">Effective 12 July 2026 · pre-release policy</p>
<p>50Hz is a free electricity-system information app. It has no account system,
advertising SDK, analytics SDK or cross-app tracking.</p>

<h2>Information you choose to provide</h2>
<p>If you use Local, the app stores your postcode on your device and sends only
its outward code (for example, SW1A) to the 50Hz API to retrieve public regional
carbon-intensity forecast data for the corresponding electricity-network region.
This is not address-level or household measurement. 50Hz does not request device
location. Clearing the app’s data or deleting the app removes its on-device
preferences and cache.</p>
<p>If you use Ask the Grid, your question and the selected grid time are sent to
the 50Hz API and then to OpenRouter so a model can explain the source-backed grid
facts. Do not put personal or confidential information in a question. 50Hz asks
OpenRouter to use Zero Data Retention-compatible processing for these requests.</p>

<h2>Service and diagnostic data</h2>
<p>Railway hosts the API and database and may process ordinary request metadata,
including IP address, time, route and user agent, to route traffic, operate logs,
protect the service and diagnose failures. 50Hz does not use that metadata to
profile users. The database contains public electricity observations and cached
event explanations, not user accounts, postcodes or question histories.</p>

<h2>Sharing and retention</h2>
<p>Data is processed only by infrastructure and AI providers needed to deliver a
feature: Railway for the API and OpenRouter and its selected model provider for
Ask or event explanations. On-device cache and preferences remain until changed,
cleared or the app is deleted. Operational request logs follow the hosting
provider’s retention controls. Public grid-source evidence is retained separately
from user requests.</p>

<h2>Your choices</h2>
<p>You can use the live national grid without entering a postcode or asking an AI
question. You can change the stored postcode at any time, avoid Ask the Grid, or
delete the app to remove local data.</p>

<h2>Contact and changes</h2>
<p>Questions or deletion concerns can be raised through the
<a href="https://github.com/papajohn-123/50hz/issues">50Hz support tracker</a>.
Material policy changes will be published on this page with a new effective date.</p>
""",
    )


@router.get("/support", response_class=HTMLResponse)
async def support() -> HTMLResponse:
    return _page(
        title="Support",
        body="""
<h1>50Hz support</h1>
<p class="updated">TestFlight and App Store support</p>
<p>50Hz turns public Elexon and NESO data into a live view of Britain’s
electricity system. It is informational and should not be used for operational,
trading, safety or emergency decisions.</p>

<h2>Before reporting a problem</h2>
<ul>
  <li>Check the data-state label in the app. Delayed and offline values are kept visibly labelled.</li>
  <li>Pull to refresh or use Retry after a connection error.</li>
  <li>For Local, enter a valid UK postcode; only the outward code leaves the device.</li>
  <li>Include the app version, iOS version, time and the screen affected. Do not include a full postcode or other personal information.</li>
</ul>

<h2>Get help</h2>
<p>Open a report in the <a href="https://github.com/papajohn-123/50hz/issues">public
50Hz issue tracker</a>. Security-sensitive reports should not include exploit
details or credentials in a public issue; open a minimal report asking for a
private contact route.</p>
""",
    )
