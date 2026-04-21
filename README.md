# Veriphone Mobile CSV Filter

Browser tool for filtering a CSV down to rows whose selected phone-number column is classified as `mobile` by Veriphone.

## What It Does

1. Upload a CSV in the browser.
2. Inspect the file locally and show a dropdown of all columns.
3. Let you choose the exact phone-number column.
4. Send the CSV to Veriphone bulk verification with `default_country=US`.
5. Download a filtered CSV containing only rows where Veriphone reports `phone_type = mobile`.

The output preserves your original columns and appends these audit fields:

- `veriphone_status`
- `veriphone_phone_valid`
- `veriphone_phone_type`
- `veriphone_e164`
- `veriphone_carrier`
- `veriphone_country_code`

## Hosted Vercel Mode

The production deployment is designed for Vercel:

- CSV uploads go directly from the browser to Vercel Blob so large files do not hit Vercel's 4.5 MB function body limit.
- The Flask app stores normalized CSVs, job manifests, and exports in Vercel Blob instead of local disk.
- Completed exports are streamed back from Blob through the app.

To run the hosted version, the Vercel project needs:

- `VERIPHONE_API_KEY`
- a connected private Vercel Blob store, which injects `BLOB_READ_WRITE_TOKEN`

The repo includes a Vercel upload-token route at `/api/blob-upload` for secure client uploads.

## Setup

```bash
cd local_workflows/03_veriphone_mobile_filter
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Add your Veriphone API key to `.env`:

```env
VERIPHONE_API_KEY=your_api_key_here
```

Local mode does not require Vercel Blob. It still uses the project's `.tmp/` folder.

## Run

From the project folder:

```bash
./veriphone start
```

If you want the same global command on your machine:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/veriphone" ~/.local/bin/veriphone
veriphone start
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000) in Chrome.

The launcher supports:

```bash
veriphone start
veriphone test
veriphone help
```

It points at this project directly, so you can run it from any folder.

## Test

```bash
veriphone test
```

## Notes

- Veriphone charges 1 credit per successfully verified row in bulk jobs.
- Rows with syntax errors are excluded from the output because only strict `mobile` rows are kept.
- If Veriphone returns unexpected CSV headers, the backend maps common aliases and fails with a clear error if no `phone_type` column can be found.
- On Vercel, uploads larger than 4.5 MB are sent directly to Blob before inspection because Vercel Functions do not accept larger request bodies.
