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
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCES = [
    ("EPGshare Italia", "https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz"),
    ("TvIt", "https://tvit.leicaflorianrobert.dev/epg/list.xml"),
    ("Pluto TV Italia", "https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/PlutoTV/it.xml.gz"),
    ("EPGshare Rakuten", "https://epgshare01.online/epgshare01/epg_ripper_RAKUTEN1.xml.gz"),
    ("EPG Italia", "https://www.epgitalia.tv/gzip"),
]

# ID equivalenti di canali italiani presenti nel catalogo Rakuten.
# Questi ID vengono usati solo per cercare il palinsesto; la M3U non cambia.
RAKUTEN_ALIASES = {
    "FailArmy.us@Italy": "IT:.Fail.Army.be",
    "RakutenTVFamilyMovies.es@Italy": "IT:.Family.-.Rakuten.TV.be",
    "ThePetCollective.us@Italy": "IT:.The.Pet.Collective.be",
    "FIFAPlus.uk@Italy": "IT:.FIFA+.be",
}
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



def parse_epgitalia_two(path: Path, required_ids: set[str]) -> tuple[dict, dict]:
    """Estrae solo TV2000 e 7 Gold dalla guida EPG Italia.

    Gli ID XMLTV esterni possono cambiare: seleziona i canali dal nome
    esposto dalla guida, usando preferibilmente una variante lombarda per 7 Gold.
    I programmi vengono rinominati agli ID originali della nostra M3U nella
    fase finale del generatore. Nessun altro canale viene alterato.
    """
    targets = {"TV2000.it@SD", "7Gold.it@SD"} & required_ids
    if not targets:
        return {}, {}

    def norm(value: str) -> str:
        value = unicodedata.normalize("NFKD", value or "")
        value = "".join(c for c in value if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]", "", value.casefold())

    def match_rank(target: str, name: str) -> int:
        n = norm(name)
        if target == "TV2000.it@SD":
            if n in ("tv2000", "tv2000hd"):
                return 5
            if n.startswith("tv2000"):
                return 2
        elif target == "7Gold.it@SD":
            if "7gold" in n and "lombardia" in n:
                return 6
            if n in ("7gold", "7goldhd", "7goldtv"):
                return 5
            if n.startswith(("7gold", "telecity7gold")):
                return 2
        return 0

    # I canali XMLTV sono normalmente dichiarati prima dei programmi.
    raw_candidates: dict[str, tuple[str, int, ET.Element]] = {}
    by_raw: dict[str, list[ET.Element]] = defaultdict(list)
    with path.open("rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    stream = gzip.open(path, "rb") if gz else path.open("rb")
    with stream:
        parser = ET.iterparse(stream, events=("start", "end"))
        _, root = next(parser)
        for event, element in parser:
            if event != "end":
                continue
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "channel":
                rawid = element.get("id", "")
                labels = [n.text or "" for n in element if n.tag.rsplit("}", 1)[-1] == "display-name"]
                # Fallback sull'ID, ma non fidarsi della sola parola "7".
                labels.append(rawid)
                best = (0, "")
                for target in targets:
                    rank = max((match_rank(target, label) for label in labels), default=0)
                    if rank > best[0]:
                        best = (rank, target)
                if best[0] and rawid:
                    raw_candidates[rawid] = (best[1], best[0], copy.deepcopy(element))
                root.clear()
            elif tag == "programme":
                rawid = element.get("channel", "")
                if rawid in raw_candidates:
                    by_raw[rawid].append(copy.deepcopy(element))
                root.clear()

    now = datetime.now(timezone.utc)
    channels, programmes = {}, {}
    for target in targets:
        matches = []
        for rawid, (mapped, rank, node) in raw_candidates.items():
            if mapped != target:
                continue
            ps = by_raw.get(rawid, [])
            fresh, count = freshness(ps, now)
            matches.append((int(fresh > 0), rank, fresh, count, rawid, node, ps))
        if not matches:
            print(f"  EPG Italia: nessun ID compatibile trovato per {target}")
            continue
        _, rank, fresh, count, rawid, node, ps = max(matches, key=lambda row: row[:4])
        if fresh == 0:
            print(f"  EPG Italia: {target} candidato {rawid} senza palinsesto recente")
            continue
        node.set("id", target)
        channels[target] = node
        programmes[target] = []
        for item in ps:
            item.set("channel", target)
            programmes[target].append(item)
        print(f"  EPG Italia: {target} <- {rawid} ({fresh} programmi recenti)")
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
        return ["Pluto TV Italia", "EPGshare Italia", "TvIt", "EPGshare Rakuten", "EPG Italia"]
    if re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)+$", channel_id):
        return ["TvIt", "EPGshare Italia", "Pluto TV Italia", "EPGshare Rakuten", "EPG Italia"]
    return ["EPGshare Italia", "TvIt", "Pluto TV Italia", "EPGshare Rakuten", "EPG Italia"]


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
                try:
                    get_file(url, local)
                except Exception as exc:
                    if name not in ("EPGshare Rakuten", "EPG Italia"):
                        raise
                    print(f"  Avviso: guida Rakuten non disponibile: {exc}")
                    src_data[name] = ({}, {})
                    continue
            required_ids = set(selected)
            if name == "EPGshare Rakuten":
                required_ids.update(RAKUTEN_ALIASES.values())
            try:
                if name == "EPG Italia":
                    channels, programmes = parse_epgitalia_two(local, required_ids)
                else:
                    channels, programmes = parse_xmltv(local, required_ids)
            except Exception as exc:
                if name != "EPG Italia":
                    raise
                print(f"  Avviso: impossibile leggere EPG Italia: {exc}")
                channels, programmes = {}, {}
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
            lookup_id = RAKUTEN_ALIASES.get(cid, cid) if source_name == "EPGshare Rakuten" else cid
            ps = programmes.get(lookup_id, [])
            fresh, count = freshness(ps, now)
            options.append((source_name, channels.get(lookup_id), ps, fresh, count, lookup_id))
        # Prima sorgente con programmi temporalmente validi; non mescola fonti
        # per lo stesso canale: evita palinsesti duplicati o sovrapposti.
        best = next((x for x in options if x[3] > 0), None)
        if best is None:
            best = next((x for x in options if x[4] > 0), None)
        if best:
            name, channel_node, programmes, fresh, count, lookup_id = best
            if channel_node is None:
                channel_node = ET.Element("channel", {"id": cid})
                ET.SubElement(channel_node, "display-name").text = names[0]
            else:
                channel_node = copy.deepcopy(channel_node)
                channel_node.set("id", cid)
            output_channels.append(channel_node)
            # Rimuove puntuali duplicati all'interno della stessa fonte.
            seen = set()
            added = 0
            for programme in programmes:
                key = (programme.get("start"), programme.get("stop"),
                       "".join(programme.itertext())[:150])
                if key in seen:
                    continue
                seen.add(key)
                programme_copy = copy.deepcopy(programme)
                programme_copy.set("channel", cid)
                output_programmes.append(programme_copy)
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
