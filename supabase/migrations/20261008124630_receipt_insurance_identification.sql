ALTER TABLE public.recibos
    ADD COLUMN IF NOT EXISTS insurance_document_type TEXT,
    ADD COLUMN IF NOT EXISTS insurance_document_number TEXT;
