#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPG Vodafone TV España: genera XMLTV (1, 2 y 7 días).

Fuentes:
  1) API Kaltura OTT de Vodafone España (api.vfp2.ott.kaltura.com). Se entra
     como usuario anónimo (partner 195) para obtener la lista de canales del
     menú "TV GUIDE" (id 100350872): nombre, dial, EPG_GUID_ID y logo.
  2) CDN pública de la guía (cdn.es.vtv.vodafone.com/epg/<epgId>/<AAAA>/<MM>/<DD>/<bloque>).
     Cada día se divide en cuatro bloques de 6 horas: 00-06, 06-12, 12-18 y
     18-00.

Uso:

  python3 epg.py --out . --days 1 2 7 [--workers 24]

Salida (junto a --out):
  epg1.xml.gz   XMLTV de 1 día   (hoy)
  epg2.xml.gz   XMLTV de 2 días  (hoy + mañana)
  epg7.xml.gz   XMLTV de 7 días  (hoy .. +6)

También escribe el .xml sin comprimir, pero el repositorio publica solo los
.xml.gz (igual que MovistarEPG). El id de canal XMLTV es el EPG_GUID_ID de
Vodafone (el mismo que usa la URL del CDN) y se añaden varios display-name
(nombre, título ES y alias) y el dial (<lcn>) para facilitar el emparejado.
"""
import argparse
import concurrent.futures
import gzip
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.sax.saxutils as sax
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MAD = ZoneInfo('Europe/Madrid')
API = 'https://api.vfp2.ott.kaltura.com/api_v3/service/'
CDN = 'https://cdn.es.vtv.vodafone.com/epg'
PARTNER_ID = 195
TV_GUIDE_CHANNEL_ID = 100350872
BLOCKS = ('00-06', '06-12', '12-18', '18-00')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) '
      'Gecko/20100101 Firefox/159.0')
TIMEOUT = 40


# ------------------------------------------------------------------ HTTP
def _post(service, payload, timeout=TIMEOUT, retries=3):
    body = json.dumps(payload).encode('utf-8')
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            API + service, data=body,
            headers={'Content-Type': 'application/json', 'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as exc:  # timeout, 5xx, red...
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last


def _get_json(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return None


# ------------------------------------------------------------------ canales
def _value(meta):
    if isinstance(meta, dict):
        return meta.get('value')
    return meta


def _tag_values(asset, key):
    tag = (asset.get('tags') or {}).get(key)
    if not tag:
        return []
    out = []
    for item in tag.get('objects') or []:
        value = item.get('value')
        if value not in (None, ''):
            out.append(str(value).strip())
    return out


def _pick_logo(asset):
    """Logo de color 16:9 (568x320) si existe; si no, el primer 16:9."""
    images = asset.get('images') or []
    candidates = [im for im in images if im.get('ratio') == '16:9' and im.get('url')]
    if not candidates:
        candidates = [im for im in images if im.get('url')]
    if not candidates:
        return ''
    # Preferimos un tamaño medio: suficiente para Kodi/TiviMate y URL corta.
    for im in candidates:
        if im.get('width') == 568 and im.get('height') == 320:
            return im['url']
    for im in candidates:
        if im.get('width') == 920 and im.get('height') == 602:
            return im['url']
    return candidates[0]['url']


def canales():
    """Lista de canales del menú TV GUIDE, sin duplicados de epgId."""
    login = _post('ottuser/action/anonymousLogin',
                  {'partnerId': PARTNER_ID, 'apiVersion': '5.3.2'})
    ks = (login.get('result') or {}).get('ks')
    if not ks:
        raise RuntimeError('No se pudo obtener un KS anónimo de Kaltura')

    data = _post('asset/action/list', {
        'filter': {'objectType': 'KalturaChannelFilter',
                   'idEqual': TV_GUIDE_CHANNEL_ID},
        'pager': {'objectType': 'KalturaFilterPager',
                  'pageSize': 500, 'pageIndex': 1},
        'apiVersion': '5.3.2', 'ks': ks,
    })
    objects = (data.get('result') or {}).get('objects') or []

    out, seen = [], set()
    for asset in objects:
        metas = asset.get('metas') or {}
        epg = _value(metas.get('EPG_GUID_ID'))
        if epg in (None, ''):
            continue
        try:
            epg = int(float(epg))
        except (TypeError, ValueError):
            continue

        if _value(metas.get('Is radio')) is True:
            continue

        name = (asset.get('name') or '').strip()
        if not name or name.upper().startswith('TEST'):
            continue
        if epg in seen:
            continue
        seen.add(epg)

        es_title = _tag_values(asset, 'ES_Title')
        aliases = _tag_values(asset, 'ChannelAKA')
        display = es_title[0] if es_title else name
        num = _value(metas.get('Channel number'))
        try:
            num = int(float(num)) if num not in (None, '') else None
        except (TypeError, ValueError):
            num = None

        names = []
        for n in [display, name] + es_title + aliases:
            n = re.sub(r'\s+', ' ', n or '').strip()
            if n and n.lower() not in {x.lower() for x in names}:
                names.append(n)

        out.append({
            'epg': epg,
            'num': num,
            'names': names[:6],
            'logo': _pick_logo(asset),
        })

    out.sort(key=lambda c: (c['num'] is None, c['num'] or 9999,
                            c['names'][0].lower() if c['names'] else ''))
    return out


# ------------------------------------------------------------------ EPG CDN
def _fetch_block(epg, day, block):
    """Programas de un canal en un bloque de 6 h. Devuelve [] si no hay guía."""
    url = '{}/{}/{:04d}/{:02d}/{:02d}/{}'.format(
        CDN, epg, day.year, day.month, day.day, block)
    last = None
    for attempt in range(3):
        try:
            data = _get_json(url)
            if not data:
                return []
            return (data.get('result') or {}).get('objects') or []
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                return []
            last = exc
        except Exception as exc:  # red, timeout, JSON roto...
            last = exc
        time.sleep(0.8 * (attempt + 1))
    if last:
        print(f'  aviso: fallo {url}: {last}', file=sys.stderr)
    return []


def _programa(asset, epg, channel_names):
    start = asset.get('startDate')
    end = asset.get('endDate')
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    if end <= start:
        return None

    metas = asset.get('metas') or {}
    title = (asset.get('name') or '').strip()
    if not title:
        return None

    sub = _value(metas.get('episode name')) or _value(metas.get('sub-title')) or ''
    sub = str(sub).strip()
    if sub.lower() == title.lower():
        sub = ''

    category = _value(metas.get('category')) or ''
    if not category:
        vals = _tag_values(asset, 'es_genre') or _tag_values(asset, 'genre')
        category = vals[0] if vals else ''

    season = _value(metas.get('season number'))
    episode = _value(metas.get('episode num')) or _value(metas.get('episode number'))
    year = _value(metas.get('year'))
    language = _value(metas.get('language')) or ''

    images = asset.get('images') or []
    icon = ''
    for im in images:
        if im.get('ratio') == '16:9' and im.get('url'):
            icon = im['url']
            break

    rating = ''
    for key in ('parental Rating', 'rating Display'):
        vals = _tag_values(asset, key)
        if vals:
            rating = vals[0]
            break

    return {
        'epg': epg,
        'channel_names': channel_names,
        'start': int(start),
        'end': int(end),
        'title': title,
        'sub': sub,
        'desc': (asset.get('description') or '').strip(),
        'category': str(category).strip(),
        'season': str(season).strip() if season is not None else '',
        'episode': str(episode).strip() if episode is not None else '',
        'year': str(year).strip() if year is not None else '',
        'language': str(language).strip(),
        'icon': icon,
        'rating': rating,
    }


def epg_canales(channels, dias, workers=24):
    """Descarga la guía de todos los canales/días/bloques en paralelo."""
    hoy = datetime.now(MAD).replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []
    for ch in channels:
        # La víspera por la noche: un programa que empieza antes de medianoche
        # y termina de madrugada vive en el bloque 18-00 del día anterior.
        tasks.append((ch, hoy - timedelta(days=1), '18-00'))
        for offset in range(dias):
            day = hoy + timedelta(days=offset)
            for block in BLOCKS:
                tasks.append((ch, day, block))

    print(f'descargando {len(tasks)} bloques de {len(channels)} canales...')
    programas = {ch['epg']: [] for ch in channels}
    hechos = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futuros = {
            pool.submit(_fetch_block, ch['epg'], day, block): (ch, day, block)
            for ch, day, block in tasks
        }
        for fut in concurrent.futures.as_completed(futuros):
            ch, day, block = futuros[fut]
            try:
                objects = fut.result()
            except Exception as exc:
                print(f'  aviso: {ch["epg"]} {day:%Y-%m-%d} {block}: {exc}',
                      file=sys.stderr)
                objects = []
            for asset in objects:
                p = _programa(asset, ch['epg'], ch['names'])
                if p:
                    programas[ch['epg']].append(p)
            hechos += 1
            if hechos % 250 == 0:
                print(f'  {hechos}/{len(tasks)} bloques')

    # dedupe y orden
    for epg, lista in programas.items():
        vistos, limpia = set(), []
        for p in sorted(lista, key=lambda x: (x['start'], x['end'], x['title'])):
            key = (p['start'], p['end'], p['title'])
            if key in vistos:
                continue
            vistos.add(key)
            limpia.append(p)
        programas[epg] = limpia

    return programas, hoy


# ------------------------------------------------------------------ XMLTV
def _fecha(ts):
    when = datetime.fromtimestamp(ts, MAD)
    return when.strftime('%Y%m%d%H%M%S'), when.strftime('%z')


def _clean(text, limit=1200):
    text = ''.join(ch for ch in str(text) if ord(ch) >= 32)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def _norm_name(text):
    """Nombre comparable: sin acentos, sin HD/UHD y sin puntuación."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r'\b(uhd|fhd|hd|sd)\b', ' ', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _esc(text):
    return sax.escape(_clean(text), {'"': '&quot;'})


def xmltv(channels, programas):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
             '<tv source-info-name="Vodafone TV España (cdn.es.vtv.vodafone.com)">']

    for ch in channels:
        if not programas.get(ch['epg']):
            continue
        cid = str(ch['epg'])
        lines.append(f'  <channel id="{cid}">')
        for name in ch['names']:
            if name:
                lines.append(f'    <display-name lang="es">{_esc(name)}</display-name>')
        if ch['num'] is not None:
            lines.append(f'    <lcn>{ch["num"]}</lcn>')
        if ch['logo']:
            lines.append(f'    <icon src="{_esc(ch["logo"])}"/>')
        lines.append('  </channel>')

    n = 0
    for ch in channels:
        cid = str(ch['epg'])
        for p in programas.get(ch['epg'], []):
            ini, off_ini = _fecha(p['start'])
            fin, off_fin = _fecha(p['end'])
            lines.append(
                f'  <programme start="{ini} {off_ini}" stop="{fin} {off_fin}" '
                f'channel="{cid}">')
            lines.append(f'    <title lang="es">{_esc(p["title"])}</title>')
            if p['sub']:
                lines.append(f'    <sub-title lang="es">{_esc(p["sub"])}</sub-title>')
            if p['desc']:
                lines.append(f'    <desc lang="es">{_esc(p["desc"])}</desc>')
            if p['category']:
                for cat in re.split(r'\s*[-;]\s*', p['category']):
                    if cat:
                        lines.append(f'    <category lang="es">{_esc(cat)}</category>')
            if p['icon']:
                lines.append(f'    <icon src="{_esc(p["icon"])}"/>')
            if p['year'].isdigit():
                lines.append(f'    <date>{p["year"]}</date>')
            if p['language']:
                lines.append(f'    <language>{_esc(p["language"])}</language>')
            if p['rating']:
                lines.append('    <rating system="es">')
                lines.append(f'      <value>{_esc(p["rating"])}</value>')
                lines.append('    </rating>')
            if p['season'].isdigit() and p['episode'].isdigit():
                season, episode = int(p['season']), int(p['episode'])
                if season > 0 and episode > 0:
                    lines.append('    <episode-num system="xmltv_ns">'
                                 f'{season - 1}.{episode - 1}.0/1</episode-num>')
            lines.append('  </programme>')
            n += 1

    lines.append('</tv>')
    return '\n'.join(lines) + '\n', n


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(
        description='EPG XMLTV de Vodafone TV España (1/2/7 días)')
    ap.add_argument('--out', default='.', help='directorio de salida')
    ap.add_argument('--days', type=int, nargs='+', default=[1, 2, 7],
                    help='ventanas en días (por defecto 1 2 7)')
    ap.add_argument('--workers', type=int, default=24,
                    help='peticiones simultáneas al CDN (por defecto 24)')
    args = ap.parse_args()

    canales_lista = canales()
    print(f'{len(canales_lista)} canales en la lista de Vodafone TV')
    programas, desde = epg_canales(
        canales_lista, max(args.days), workers=args.workers)

    # Varias señales pueden compartir dial y nombre (p. ej. La 2 / La 2 HD o
    # las dos copias de Canal 24 Horas). Nos quedamos con la que más guía trae.
    mejores = {}
    for ch in canales_lista:
        key = (ch['num'], _norm_name(ch['names'][0] if ch['names'] else ''))
        count = len(programas.get(ch['epg'], []))
        if key not in mejores or count > mejores[key][0]:
            mejores[key] = (count, ch)
    canales_lista = sorted(
        (item[1] for item in mejores.values()),
        key=lambda c: (c['num'] is None, c['num'] or 9999,
                       c['names'][0].lower() if c['names'] else ''))
    for ch in canales_lista:
        programas.setdefault(ch['epg'], [])
    programas = {ch['epg']: programas[ch['epg']] for ch in canales_lista}

    con_guia = [c for c in canales_lista if programas.get(c['epg'])]
    print(f'canales con guía: {len(con_guia)} | sin guía: '
          f'{len(canales_lista) - len(con_guia)}')
    if not con_guia:
        raise RuntimeError('No se ha descargado ningún programa')

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for dias in sorted(set(args.days)):
        limite = desde + timedelta(days=dias)
        ventana = {}
        for ch in con_guia:
            lista = []
            for p in programas[ch['epg']]:
                ini = datetime.fromtimestamp(p['start'], MAD)
                fin = datetime.fromtimestamp(p['end'], MAD)
                if ini < limite and fin > desde:
                    lista.append(p)
            ventana[ch['epg']] = lista
        xml, nprog = xmltv(con_guia, ventana)
        gz = out / f'epg{dias}.xml.gz'
        with gzip.open(gz, 'wt', encoding='utf-8') as fh:
            fh.write(xml)
        (out / f'epg{dias}.xml').write_text(xml, encoding='utf-8')
        print(f'epg{dias}: {nprog} programas -> {gz} ({gz.stat().st_size} bytes)')

    return 0


if __name__ == '__main__':
    sys.exit(main())
