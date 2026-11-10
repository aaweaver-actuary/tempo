-- Original first responses are retained evidence, including every identity and clock.
CREATE FUNCTION public.reject_position_first_response_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'study_position_first_response_immutable';
END;
$$;

CREATE TRIGGER study_position_first_response_immutable_row
BEFORE UPDATE OR DELETE ON public.study_position_first_responses
FOR EACH ROW EXECUTE FUNCTION public.reject_position_first_response_mutation();

CREATE TRIGGER study_position_first_response_immutable_truncate
BEFORE TRUNCATE ON public.study_position_first_responses
FOR EACH STATEMENT EXECUTE FUNCTION public.reject_position_first_response_mutation();

INSERT INTO public.tempo_schema_migrations(version) VALUES (60);
