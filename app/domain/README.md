# Frontend domain

This is the strongest frontend boundary. It contains product types, branded
chess primitives, transport models, Zod schemas, and adapters that turn
untrusted API/worker records into safe application values.

Domain code should not import React or reach directly into browser storage. Add
schemas and adapter tests when a backend or worker contract changes.
