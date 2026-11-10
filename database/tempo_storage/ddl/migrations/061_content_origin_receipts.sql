-- Historical semantic source observations only; no Content or currentness authority.
CREATE TABLE public.legacy_content_historical_origins (
    legacy_target_id text NOT NULL,
    legacy_workspace_id text NOT NULL,
    export_id text NOT NULL,
    receipt_id text NOT NULL UNIQUE,
    payload bytea NOT NULL,
    receipt_digest text NOT NULL,
    PRIMARY KEY (legacy_target_id, legacy_workspace_id, export_id)
);
CREATE FUNCTION public.reject_content_historical_origin_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'legacy_content_historical_origin_immutable';
END $$;
CREATE TRIGGER content_historical_origin_immutable_row
BEFORE UPDATE OR DELETE ON public.legacy_content_historical_origins
FOR EACH ROW EXECUTE FUNCTION public.reject_content_historical_origin_mutation();
CREATE TRIGGER content_historical_origin_immutable_truncate
BEFORE TRUNCATE ON public.legacy_content_historical_origins
FOR EACH STATEMENT EXECUTE FUNCTION public.reject_content_historical_origin_mutation();
INSERT INTO public.tempo_schema_migrations(version) VALUES (61);
