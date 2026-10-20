CREATE TABLE IF NOT EXISTS analysis_exports(input_id text PRIMARY KEY,workspace_id text NOT NULL,manifest jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_export_pages(input_id text NOT NULL REFERENCES analysis_exports(input_id),kind text NOT NULL,page_index integer NOT NULL,digest text NOT NULL,document jsonb NOT NULL,PRIMARY KEY(input_id,kind,page_index));
INSERT INTO evidence_schema_migrations(version) VALUES(3);
