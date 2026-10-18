# Frontend map dependencies

The application serves its map libraries locally so production does not depend on a third-party
CDN at page load. The checked-in versions are:

- Leaflet 1.9.4;
- MapLibre GL JS 6.10.0;
- MapLibre GL Leaflet 0.1.4;
- Swagger UI 5.33.1.

Files were downloaded from their versioned `unpkg.com` package paths and checked against the
SHA-256 values returned by the unpkg metadata API. The adapter's two import specifiers were changed
to relative paths for these local copies; its implementation is otherwise unchanged. Swagger UI
is used only when `APP_ENABLE_DOCS=true`. Each package directory contains its upstream license.

When updating a package, replace its distribution files and license together, verify their hashes,
then test map initialization and the MapLibre worker request in a browser.
