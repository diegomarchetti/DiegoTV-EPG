#!/usr/bin/env python3
# coding: utf-8
"""Unisce tre sorgenti XMLTV, mantenendo solo i canali scelti in DiegoTV.m3u.

Usa soltanto librerie standard di Python, senza abbonamenti o dipendenze esterne.
Riconosce tvg-id (EPGshare / TvIt / Pluto) ed esclude programmi duplicati.
Non modifica gli indirizzi degli streaming della playlist.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCES = [
    ("EPGshare Italia", "https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz"),
    ("TvIt", "https://tvit.leicaflorianrobert.dev/epg/list.xml"),
    ("Pluto TV Italia", "https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/PlutoTV/it.xml.gz"),
]
USER_AGENT = "DiegoTV-EPG/1.0 (XMLTV guide merger; Python urllib)"
ID_RE = re.compile(r'\btvg-id="([^"]*)"')
PLUTO_RE = re.compile(r"^[0-9a-f]{24}$", re.I)


def parse_playlist(path: Path) -> dict[str, list[str]]:
    """Canali scelti, nell'ordine M3U. Una chiave è il tvg-id esatto."""
    items: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("#EXTINF:"):
            continue
        match = ID_RE.search(line)
        if not match or not match.group(1).strip():
            continue
        cid = match.group(1).strip()
        name = line.rsplit(",", 1)[-1].strip()
        items.setdefault(cid, []).append(name)
    return items


def get_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=90) as response, dest.open("wb") as output:
        import shutil
        shutil.copyfileobj(response, output, length=1024 * 1024)
    if dest.stat().st_size < 100:
        raise ValueError("File troppo piccolo: potrebbe essere una pagina di errore")


def parse_xmltv(path: Path, required_ids: set[str]) -> tuple[dict, dict]:
    """Legge lo stream XML senza caricare in memoria tutti i canali delle guide."""
    with path.open("rb") as fh:
        is_gzip = fh.read(2) == b"\x1f\x8b"
    stream = gzip.open(path, "rb") if is_gzip else path.open("rb")
    channels: dict[str, ET.Element] = {}
    programmes: dict[str, list[ET.Element]] = defaultdict(list)
    try:
        with stream:
            parser = ET.iterparse(stream, events=("start", "end"))
            _, root = next(parser)
            for event, element in parser:
                if event != "end":
                    continue
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "channel":
                    cid = element.get("id", "")
                    if cid in required_ids:
                        channels[cid] = copy.deepcopy(element)
                    root.clear()
                elif tag == "programme":
                    cid = element.get("channel", "")
                    if cid in required_ids:
                        programmes[cid].append(copy.deepcopy(element))
                    root.clear()
    except ET.ParseError as ex:
        raise ValueError(f"XMLTV non valido: {ex}") from ex
    return channels, programmes


def parse_date(text: str | None) -> datetime | None:
    """Gestisce date XMLTV tipo '20261120130000 +0100'."""
    if not text:
        return None
    m = re.match(r"^(\d{14}|\d{12})(?:\s+([+-]\d{4}))?", text)
    if not m:
        return None
    stamp, off = m.groups()
    try:
        d = datetime.strptime(stamp, "%Y%m%d%H%M%S" if len(stamp) == 14 else "%Y%m%d%H%M")
        if off:
            hrs, mins = int(off[1:3]), int(off[3:5])
            delta = timedelta(hours=hrs, minutes=mins)
            d = d.replace(tzinfo=timezone(delta if off[0] == "+" else -delta))
        else:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


def freshness(programmes: list[ET.Element], now: datetime) -> tuple[int, int]:
    """Verifica che i palinsesti non siano solo dati storici."""
    min_t = now - timedelta(hours=12)
    max_t = now + timedelta(days=8)
    in_window = sum(1 for p in programmes if (d := parse_date(p.get("start"))) and min_t <= d <= max_t)
    return in_window, len(programmes)


def source_priority(channel_id: str) -> list[str]:
    if PLUTO_RE.fullmatch(channel_id):
        return ["Pluto TV Italia", "EPGshare Italia", "TvIt"]
    if re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)+$", channel_id):
        return ["TvIt", "EPGshare Italia", "Pluto TV Italia"]
    return ["EPGshare Italia", "TvIt", "Pluto TV Italia"]


def build(playlist: Path, out: Path, report: Path, overrides: list[tuple[str, Path]] | None = None) -> None:
    selected = parse_playlist(playlist)
    if not selected:
        raise RuntimeError("Nessun tvg-id nella playlist M3U")
    print(f"Canali M3U con tvg-id: {sum(map(len, selected.values()))} ({len(selected)} ID distinti)")

    with tempfile.TemporaryDirectory(prefix="diegotv-epg-") as temp:
        src_data = {}
        for index, (name, url) in enumerate(SOURCES):
            local = Path(temp) / f"source_{index}.xmltv"
            if overrides:
                matched = [p for k, p in overrides if k == name]
                if matched:
                    local = matched[0]
            if local.parent == Path(temp):
                print(f"Scarico {name}: {url}")
                get_file(url, local)
            channels, programmes = parse_xmltv(local, set(selected))
            print(f"  ID presenti: {len(channels)}; palinsesti per {len(programmes)} ID")
            src_data[name] = (channels, programmes)

    now = datetime.now(timezone.utc)
    root = ET.Element("tv", attrib={"generator-info-name": "DiegoTV EPG unificata"})
    output_rows = []
    output_channels = []
    output_programmes = []
    total_programmes = 0
    for cid, names in selected.items():
        options = []
        for source_name in source_priority(cid):
            channels, programmes = src_data[source_name]
            ps = programmes.get(cid, [])
            fresh, count = freshness(ps, now)
            options.append((source_name, channels.get(cid), ps, fresh, count))
        # Prima sorgente con programmi temporalmente validi; non mescola fonti
        # per lo stesso canale: evita palinsesti duplicati o sovrapposti.
        best = next((x for x in options if x[3] > 0), None)
        if best is None:
            best = next((x for x in options if x[4] > 0), None)
        if best:
            name, channel_node, programmes, fresh, count = best
            if channel_node is None:
                channel_node = ET.Element("channel", {"id": cid})
                ET.SubElement(channel_node, "display-name").text = names[0]
            output_channels.append(copy.deepcopy(channel_node))
            # Rimuove puntuali duplicati all'interno della stessa fonte.
            seen = set()
            added = 0
            for programme in programmes:
                key = (programme.get("start"), programme.get("stop"),
                       "".join(programme.itertext())[:150])
                if key in seen:
                    continue
                seen.add(key)
                output_programmes.append(copy.deepcopy(programme))
                added += 1
            total_programmes += added
            output_rows.append((", ".join(names), cid, "OK" if fresh else "DATI NON RECENTI", name, added))
        else:
            # Mantiene l'ID riconoscibile, ma non inventa programmazioni.
            node = next((x[1] for x in options if x[1] is not None), None)
            if node is None:
                node = ET.Element("channel", {"id": cid})
                ET.SubElement(node, "display-name").text = names[0]
            output_channels.append(copy.deepcopy(node))
            output_rows.append((", ".join(names), cid, "EPG MANCANTE", "", 0))

    if total_programmes == 0:
        raise RuntimeError("Nessun programma trovato: non sovrascrivo una guida valida")
    # XMLTV richiede tutti i <channel> prima di tutti i <programme>.
    root.extend(output_channels)
    root.extend(output_programmes)
    out.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    temp_out = out.with_name(out.name + ".tmp")
    with gzip.open(temp_out, "wb", compresslevel=6) as dest:
        dest.write(payload)
    temp_out.replace(out)
    with report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(("Canale", "tvg-id", "Stato", "Fonte scelta", "Programmi"))
        writer.writerows(output_rows)
    covered = sum(1 for x in output_rows if x[2] == "OK")
    missing = sum(1 for x in output_rows if x[2] == "EPG MANCANTE")
    print(f"EPG creata: {out} ({out.stat().st_size // 1024} KB); {total_programmes} programmi")
    print(f"Canali coperti con programmi recenti: {covered}/{len(selected)}. Senza guida: {missing}")
    print(f"Resoconto: {report}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea EPG unica per DiegoTV")
    parser.add_argument("--playlist", type=Path, default=Path("DiegoTV.m3u"))
    parser.add_argument("--output", type=Path, default=Path("docs/epg.xml.gz"))
    parser.add_argument("--report", type=Path, default=Path("docs/report.csv"))
    args = parser.parse_args()
    try:
        build(args.playlist, args.output, args.report)
        return 0
    except Exception as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
