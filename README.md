# Wanderplan — bezpieczny planer tras zwiedzania

Wanderplan układa wielodniowe trasy na podstawie sieci pieszej i punktów atrakcji. Backend korzysta z FastAPI i Shapely, a interfejs z Leaflet i MapLibre.

## Model danych i uprawnienia

Aplikacja publiczna udostępnia wyłącznie zestawy przygotowane i opublikowane przez administratora. Użytkownik nie może wgrywać, tworzyć ani usuwać zestawów. Może zmieniać aktywność oraz czas zwiedzania atrakcji, ale te ustawienia są przechowywane tylko w IndexedDB jego przeglądarki i nie modyfikują publicznego pliku.

Narzędzie tworzenia zestawów jest dostępne wyłącznie administratorowi korzystającemu z aplikacji przez adres loopback (`localhost` / `127.0.0.1`). Produkcyjny interfejs ukrywa formularz, a API odrzuca surowe dane również wtedy, gdy ktoś spróbuje wysłać je ręcznie.

## Uruchomienie lokalne

Wymagany jest Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Aplikacja działa pod `http://localhost:8000`. Pod tym adresem pojawia się przycisk „Przygotuj zestaw administratorski”. Po wskazaniu plików GeoJSON backend buduje graf oraz macierz kosztów i zapisuje wynik w `data/prepared`. Dokumentacja API jest domyślnie wyłączona; lokalnie można ją włączyć przez `APP_ENABLE_DOCS=true`.

Testy:

```powershell
python -m unittest discover -s tests -v
```

Docker:

```powershell
docker build -t wanderplan .
docker run --rm -p 8000:8000 wanderplan
```

Kontener działa jako użytkownik bez uprawnień administratora i ma kod aplikacji tylko do odczytu.

## Publikowanie publicznych zestawów na Vercelu

Pliki w `data/prepared` są duże i nie powinny być zapisywane przez funkcję Vercel. Zalecany układ to [publiczny Vercel Blob](https://vercel.com/docs/vercel-blob/public-storage) używany wyłącznie jako magazyn odczytu oraz mały manifest ze skrótami integralności.

1. Uruchom aplikację pod `http://localhost:8000`, wybierz „Przygotuj zestaw administratorski”, wczytaj dane i zaczekaj na zapis pliku `data/prepared/IDENTYFIKATOR.json`.
2. W panelu projektu Vercel utwórz magazyn Blob z dostępem **Public** i połącz go z projektem.
3. Połącz lokalny katalog z projektem (`vercel link`), a następnie prześlij przygotowany plik. Dla dużych plików CLI domyślnie używa uploadu multipart:

   ```powershell
   vercel blob put .\data\prepared\IDENTYFIKATOR.json
   ```

   Zapisz publiczny adres URL wypisany przez CLI. Token zapisu trzymaj wyłącznie w lokalnym środowisku publikującym i nigdy w frontendzie. Po publikacji aplikacja potrzebuje tylko publicznych adresów HTTPS — usuń `BLOB_READ_WRITE_TOKEN` ze środowiska produkcyjnej funkcji albo odłącz magazyn od projektu, jeżeli Vercel dodał token automatycznie.
4. Oblicz SHA-256 przesłanego pliku:

   ```powershell
   Get-FileHash -Algorithm SHA256 .\data\prepared\IDENTYFIKATOR.json
   ```

5. Dodaj wpis do manifestu według [public-networks.example.json](public-networks.example.json). Przepisz metadane z `data/prepared/IDENTYFIKATOR.meta.json`, wstaw URL z kroku 3 i SHA-256 z kroku 4. Zachowaj w manifeście również wcześniejsze zestawy.
6. Oblicz SHA-256 gotowego manifestu i prześlij go do Blob pod nową, wersjonowaną nazwą:

   ```powershell
   Get-FileHash -Algorithm SHA256 .\public-networks.json
   vercel blob put .\public-networks.json
   ```

7. Ustaw w Vercelu wartości zwróconego URL-u i obliczonego skrótu, a następnie wykonaj nowe wdrożenie:

   ```text
   APP_PUBLIC_NETWORK_MANIFEST_URL=https://...public.blob.vercel-storage.com/public-networks.json
   APP_PUBLIC_NETWORK_MANIFEST_SHA256=<sha256 manifestu>
   APP_ALLOWED_HOSTS=twoja-domena.pl,www.twoja-domena.pl
   ```

Backend akceptuje tylko HTTPS i dozwolone hosty, weryfikuje sumę manifestu oraz każdego pliku przed deserializacją i trzyma w pamięci najwyżej jeden duży zestaw. Domyślnie dozwolone są hosty kończące się na `.public.blob.vercel-storage.com`; własny host można dodać przez `APP_PUBLIC_DATA_HOSTS`.

Bez zmiennych manifestu aplikacja czyta lokalny `data/prepared` w trybie tylko do odczytu. To jest wygodne lokalnie, ale ignorowane pliki nie trafią automatycznie do wdrożenia Git na Vercelu.

## Konfiguracja bezpieczeństwa

Przykład znajduje się w [.env.example](.env.example). Dostępne ustawienia:

- `APP_ALLOWED_HOSTS` — domeny produkcyjne akceptowane w nagłówku `Host`;
- `APP_MAX_REQUEST_BYTES` — maksymalny rozmiar JSON-u wejściowego; `0` wyłącza limit aplikacji (domyślnie lokalnie), natomiast na Vercelu domyślne jest 4 MB;
- `APP_MAX_CONCURRENT_PLANS` — limit równoległych obliczeń w jednej instancji;
- `APP_PLAN_REQUESTS_PER_MINUTE` — pomocniczy limit żądań na klienta w jednej instancji;
- `APP_ENABLE_DOCS` — włącza `/docs` i `/openapi.json`;
- `APP_ALLOW_LOCAL_ADMIN` — opcjonalne jawne włączenie lokalnego narzędzia administratora; na Vercelu domyślnie wyłączone, a endpoint i tak wymaga połączenia loopback;
- `APP_PUBLIC_NETWORK_MANIFEST_URL` i `APP_PUBLIC_NETWORK_MANIFEST_SHA256` — katalog publicznych danych;
- `APP_PUBLIC_DATA_HOSTS` — lista hostów danych, rozdzielona przecinkami;
- `APP_MAX_PUBLIC_NETWORK_BYTES` — limit pojedynczego publicznego pliku.

Po wdrożeniu należy dodatkowo ustawić [regułę Vercel Firewall/WAF](https://vercel.com/docs/vercel-firewall/vercel-waf/custom-rules) ograniczającą `POST /api/plan`. Limit w aplikacji działa osobno w każdej instancji serverless i nie zastępuje limitu na brzegu sieci.

Aplikacja ustawia CSP, HSTS, ochronę przed osadzaniem w ramce, `nosniff`, brak referrera oraz `Cache-Control: no-store` dla API. Zewnętrzne skrypty i style mają przypięte wersje i SRI. Przy aktualizacji bibliotek CDN trzeba zaktualizować również ich skróty `integrity`.

## Duże zestawy danych

Lokalne narzędzie administratora nie narzuca stałego limitu liczby obiektów sieci, atrakcji ani współrzędnych. Rzeczywistą granicą pozostają pamięć, miejsce na dysku i czas obliczeń komputera. Do Vercela trafia dopiero przygotowany plik przez Blob, a publiczne planowanie przesyła tylko identyfikator zestawu i niewielkie ustawienia. Plan może obejmować najwyżej 50 atrakcji must-see łącznie.

## API

Publiczna powierzchnia HTTP jest celowo mała:

- `GET /api/health` — kontrola działania;
- `GET /api/networks` — metadane publicznych zestawów tylko do odczytu;
- `GET /api/networks/{id}/attractions` — dane tabeli atrakcji tylko do odczytu;
- `POST /api/plan` — synchroniczne, bezstanowe obliczenie trasy.

Lokalny `POST /api/admin/networks` działa tylko z połączenia loopback i nie jest dostępny na Vercelu. Nie istnieją publiczne endpointy `POST`, `PUT`, `PATCH` ani `DELETE` dla katalogu zestawów.

## Najważniejsze pliki

- `main.py` — minimalne API HTTP i ograniczenia żądań;
- `security.py` — limit rozmiaru, nagłówki bezpieczeństwa i lokalny rate limit;
- `public_catalog.py` — publiczne dane tylko do odczytu, allowlista HTTPS i SHA-256;
- `network_preparation.py` — walidacja GeoJSON i budowa grafu;
- `route_planning.py` — planowanie i bezpieczna odpowiedź;
- `prepared_store.py` — lokalny format przygotowanych sieci z trybem read-only;
- `static/js/local-store.js` — prywatny magazyn ustawień atrakcji w IndexedDB;
- `vercel.json` — konfiguracja funkcji Vercel.
