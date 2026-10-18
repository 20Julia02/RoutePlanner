"use strict";

globalThis.SwaggerUIBundle({
  url: "/openapi.json",
  dom_id: "#swagger-ui",
  deepLinking: true,
  displayRequestDuration: true,
  presets: [globalThis.SwaggerUIBundle.presets.apis],
  layout: "BaseLayout",
});
