# VodafoneEPG

Guía XMLTV de Vodafone TV España generada a partir de la API Kaltura OTT de
Vodafone (`api.vfp2.ott.kaltura.com`) y de la CDN pública de la guía
(`cdn.es.vtv.vodafone.com/epg`).

## Ficheros publicados

| Fichero | Ventana | Contenido aprox. |
| --- | --- | --- |
| `epg1.xml.gz` | 1 día (hoy) | ~4.400 pases / ~150 canales |
| `epg2.xml.gz` | 2 días (hoy + mañana) | ~8.500 pases |
| `epg7.xml.gz` | 7 días (hoy .. +6) | ~28.000 pases |

Los tres se regeneran cada día y se publican en este repositorio. Los canales
quedan identificados por el `EPG_GUID_ID` de Vodafone (el mismo número que usa
la URL de la CDN: `epg/7754/...`, `epg/8522/...`, etc.). Cada `<channel>`
incluye:

- varios `<display-name>`: nombre comercial, título en español y alias
  (`La 1`, `La 1 HD`, `TVE 1`, `RTVE 1`...), para que el reproductor pueda
  emparejar por cualquiera de ellos;
- `<lcn>` con el dial de Vodafone;
- `<icon>` con el logo de color del canal.

Los `.xml` sin comprimir se generan junto a los `.xml.gz`, pero no se suben al
repositorio (igual que en MovistarEPG) para no engordar el historial de Git.

## Uso del script

```bash
python3 epg.py --out . --days 1 2 7 [--workers 32]
```

- `--out` directorio de salida.
- `--days` ventanas en días; escribe `epg1.xml.gz`, `epg2.xml.gz`, ... y su
  equivalente `.xml` sin comprimir.
- `--workers` peticiones simultáneas al CDN (por defecto 24).

No hacen falta credenciales ni login: la lista de canales se obtiene con un
`anonymousLogin` de Kaltura (partner 195) y la guía es pública en la CDN. Por
eso el script se puede ejecutar desde cualquier servidor con salida a Internet.

## Consumo

En la lista M3U, la cabecera apunta al fichero deseado:

```
#EXTM3U url-tvg="https://raw.githubusercontent.com/mateodd1/VodafoneEPG/main/epg7.xml.gz"
```

También se puede usar directamente el `.xml.gz` como fuente EPG en Kodi,
TiviMate, TVHeadend, Jellyfin, etc.

## Renovación

Ejecuta el script con cron (por ejemplo, cada día a las 04:20) y haz
commit + push de los tres `.xml.gz` resultantes. El script incluye la víspera
por la noche para que los programas que empiezan antes de medianoche y acaban
de madrugada no dejen huecos al principio del día.
