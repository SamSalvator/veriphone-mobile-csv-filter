# Veriphone Mobile CSV Filter

Local browser tool for filtering a CSV down to rows whose selected phone-number column is classified as `mobile` by Veriphone.

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
