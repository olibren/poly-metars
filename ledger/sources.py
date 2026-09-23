"""Pure government directory parsing shared by collection and offline tests."""

from datetime import datetime
from html.parser import HTMLParser
import re
from .metar import UTC, report_time


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            value = dict(attrs).get("href", "")
            if value and not value.startswith(("?", "/", "#")) and ".." not in value:
                self.links.append(value)

def eccc_live_links(links, prefixes, reference=None):
    """Keep all versions of the two newest bulletin times per route; recovery scans all."""
    reference = reference or datetime.now(UTC)
    chosen = set()
    for prefix in prefixes:
        matches = [name for name in links if name.startswith(prefix) and len(name.split("_")) > 2]
        times = sorted({name.split("_")[2] for name in matches}, key=lambda value: report_time(value, reference), reverse=True)[:2]
        chosen.update(name for name in matches if name.split("_")[2] in times)
    return sorted(chosen, key=lambda name: report_time(name.split("_")[2], reference), reverse=True)

def collective_listing(text):
    """Use advertised receipt dates, never the rotating filename as a chronology."""
    entries = []
    for row in re.findall(r"<tr>.*?</tr>", text, re.S):
        name = re.search(r'href="(sn\.\d{4}\.txt)"', row)
        stamp = re.search(r"(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})", row)
        if name and stamp:
            modified = datetime.strptime(stamp[1], "%d-%b-%Y %H:%M").replace(tzinfo=UTC)
            entries.append((name[1], modified))
    if not entries:
        raise ValueError("TGFTP collective directory has no recognized timestamped files")
    return sorted(entries, key=lambda entry: entry[1], reverse=True)
