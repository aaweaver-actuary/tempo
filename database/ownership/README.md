# Disposable database ownership fixture

Existing cross-owner PostgreSQL role-isolation test fixture, relocated intact from `contracts/database-ownership`. It creates task-owned synthetic schemas/roles only when explicitly run. It is test infrastructure, not a shared production runtime or another production store. The fixture and assertions are unchanged. No fixture was started by this file-move task. [Database index](../README.md).
