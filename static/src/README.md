# Static entry point

`main.tsx` mounts the shared Tempo application for the `/tempo/` base path,
loads shared styles, installs the error boundary, and registers the service
worker in production. Keep runtime-specific bootstrapping here and product
behavior in `app`.
