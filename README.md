# DiegoTV – Guida EPG unificata

Questo progetto scarica e unisce tre sorgenti XMLTV gratuite in **un solo file EPG**:

1. EPGshare01 Italia: `epg_ripper_IT1.xml.gz`
2. TvIt: `epg/list.xml`
3. Pluto TV Italia, progetto i.mjh.nz: `PlutoTV/it.xml.gz`

Usa **soltanto gli ID già presenti** nella playlist `DiegoTV.m3u` (105 canali), mantenendo un solo palinsesto per canale secondo la priorità stabilita. L'EPG generata non modifica gli URL degli streaming.

## Verifica sul PC Windows

- Richiede Python 3.10 o superiore e una connessione Internet.
- Fai doppio clic su `AVVIA_SU_WINDOWS.bat`.
- Al termine trovi `docs/epg.xml.gz` e `docs/report.csv`.
- Se una fonte non risponde, viene segnalato un errore e l'eventuale EPG precedente non è sovrascritta.

Il risultato è una **fotografia temporanea** dei palinsesti. Per aggiornamenti automatici usa GitHub Actions.

## Pubblicazione online gratuita tramite GitHub

1. Crea un nuovo repository **pubblico** su GitHub, ad esempio `DiegoTV-EPG`.
2. Carica nella radice del repository tutti i file e le cartelle contenuti in questo progetto, compresa `.github/workflows` (non caricare lo ZIP come file unico).
3. Nelle impostazioni del repository, in **Actions → General → Workflow permissions**, consenti il permesso di scrittura se necessario. Il workflow richiede `contents: write`.
4. Apri **Actions → Aggiorna EPG DiegoTV → Run workflow** e verifica che termini correttamente.
5. L'indirizzo da inserire in Televizo sarà:

   `https://raw.githubusercontent.com/TUO_USERNAME/DiegoTV-EPG/main/docs/epg.xml.gz`

   Sostituisci `TUO_USERNAME` e il nome del repository, se lo hai scelto diverso.

La guida viene rigenerata ogni giorno. Non serve GitHub Pages: Televizo può scaricarla direttamente dal collegamento pubblico GitHub *se supporta XMLTV gzip via URL*.

## Importante

- In Televizo usa **una sola sorgente EPG**, l'URL della guida generata; puoi rimuovere gli indirizzi singoli dopo che il file è online e verificato.
- La playlist M3U rimane separata: non confondere i collegamenti ai canali con il collegamento EPG.
- Se un canale rimane senza palinsesto, guarda il CSV `docs/report.csv`. Alcuni canali locali, FAST o radio-TV non sono presenti nelle tre sorgenti oppure hanno identificativi diversi.
- La guida pubblicata è ricavata da fonti di terzi: disponibilità e aggiornamenti non sono garantiti.
- Verifica le condizioni d'uso e la liceità dei dati delle fonti prima di redistribuire il file in un repository pubblico.
- I file generati non sono stati scaricati e collaudati in tempo reale in questa sessione: il programma è verificato con dati di esempio, il primo test effettivo si fa sul tuo PC.
