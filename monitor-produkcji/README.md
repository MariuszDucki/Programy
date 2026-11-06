# Monitor Produkcji – monitoring linii w czasie rzeczywistym

Własny, otwarty system klasy **MES-light / OEE** do monitorowania produkcji na żywo
(nie jest to kopia żadnego produktu komercyjnego). Funkcje:

- **Widok hali (TV/andon):** kafelki linii z aktualnym stanem, czasem trwania stanu,
  OEE (Dostępność × Wydajność × Jakość), prędkością vs nominał i wykonaniem planu zmiany.
  Odświeżanie na żywo przez WebSocket co 2 s.
- **Szczegóły linii:** oś czasu zmiany (praca / postoje nieplanowane / planowane, np. CIP),
  produkcja godzinowa vs cel, lista postojów z **przypisywaniem przyczyn przez operatora**.
- **Alarmy:** długi postój nieplanowany, spadek prędkości, przekroczony poziom braków
  – z potwierdzaniem przez operatora i historią.
- **Raport zmiany:** KPI per linia + **Pareto przyczyn postojów**, eksport CSV (Excel).
- **API dla maszyn/PLC:** prosty REST – podłączysz go przez Node-RED, Kepware, bramkę OPC UA/MQTT.
- **Symulator** linii do demo i szkoleń.

## Uruchomienie

```bash
cd monitor-produkcji
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 -m app.simulator --hours 8      # opcjonalnie: dane demo z ostatnich 8 h
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Otwórz http://localhost:8000 (na hali – telewizor z przeglądarką w trybie pełnoekranowym).

### Zmienne środowiskowe

| Zmienna     | Domyślnie        | Opis |
|-------------|------------------|------|
| `SIMULATOR` | `1`              | `0` = wyłącz symulator (tryb produkcyjny, dane tylko z API) |
| `API_KEY`   | brak             | jeśli ustawiony, wymagany w polu `api_key` przy zapisie stanów/liczników |
| `DB_PATH`   | `produkcja.db`   | plik bazy SQLite |
| `CONFIG`    | `config.json`    | konfiguracja linii, zmian, stanów, przyczyn i progów alarmów |

## Konfiguracja (`config.json`)

- `lines` – linie: `id`, `name`, `product`, `ideal_rate_per_min` (prędkość nominalna), `target_oee`
- `shifts` – zmiany (obsługuje zmianę nocną przez północ)
- `states` – stany maszyn i ich kategoria: `run`, `unplanned`, `planned`
  (postoje `planned`, np. CIP, nie obniżają dostępności)
- `stop_reasons` – słownik przyczyn postojów do wyboru przez operatora
- `alarms` – progi alarmów

## Integracja z maszynami (PLC)

Bramka (Node-RED / skrypt / SCADA) wysyła dwa typy zdarzeń:

```bash
# zmiana stanu linii
curl -X POST localhost:8000/api/lines/L1/state -H 'Content-Type: application/json' \
     -d '{"state":"STOP","reason":"Zacięcie / zator"}'

# przyrost liczników (np. co 5–60 s, różnica od ostatniego odczytu)
curl -X POST localhost:8000/api/lines/L1/count -H 'Content-Type: application/json' \
     -d '{"good":120,"scrap":2}'
```

Pozostałe endpointy: `GET /api/snapshot`, `GET /api/lines/{id}`, `GET /api/alarms?active=true`,
`POST /api/alarms/{id}/ack`, `PUT /api/stops/{id}/reason`, `GET /api/report?date=YYYY-MM-DD&shift=I`,
`GET /api/report.csv`, `WS /ws`. Pełna dokumentacja interaktywna: http://localhost:8000/docs

## Definicja OEE

- **Dostępność** = czas pracy / (czas zmiany − postoje planowane)
- **Wydajność** = (sztuki ogółem / prędkość nominalna) / czas pracy
- **Jakość** = sztuki dobre / sztuki ogółem
- Czas bez żadnego sygnału z linii liczony jest jako postój nieplanowany (podejście konserwatywne).

## Testy

```bash
python3 -m pytest -q
```

## Możliwe kolejne kroki

Logowanie użytkowników i role (operator / lider / kierownik), zlecenia produkcyjne i zmiany SKU,
konektor OPC UA / MQTT wbudowany w aplikację, PostgreSQL/TimescaleDB zamiast SQLite,
mikropostoje i analiza strat (6 Big Losses), powiadomienia SMS/Teams.
