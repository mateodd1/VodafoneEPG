# VodafoneEPG

Guía XMLTV de Vodafone TV España en ventanas de 1, 2 y 7 días.

| Fichero | Ventana | Contenido aprox. |
| --- | --- | --- |
| `epg1.xml.gz` | 1 día (hoy) | ~4.400 pases |
| `epg2.xml.gz` | 2 días (hoy + mañana) | ~8.500 pases |
| `epg7.xml.gz` | 7 días (hoy .. +6) | ~26.000 pases |

Los tres se regeneran cada día (04:10) y se publican en este repositorio.
El id de canal XMLTV es el `EPG_GUID_ID` de Vodafone (el de la URL del CDN)
y cada canal incluye varios `display-name`, el dial (`<lcn>`) y su logo.
Los `.xml` sin comprimir no se publican.

## Consumo

```
#EXTM3U url-tvg="https://raw.githubusercontent.com/mateodd1/VodafoneEPG/main/epg7.xml.gz"
```

El generador es privado; este repositorio solo contiene los datos.
