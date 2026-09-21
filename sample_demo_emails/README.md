# Sample Emails & Attachments for Live Gmail Demo

This directory contains ready-to-send email templates and trade documents for testing and demonstrating NavisAI's live Gmail ingestion listener.

---

## Available Scenarios

### 🟢 Scenario 1: Clean Verification (100% Match)
* **Folder**: [`01_clean_match_msc/`](./01_clean_match_msc/)
* **Carrier**: MSC Mediterranean Shipping
* **Booking**: `MEDU-9824101`
* **Attachments to attach**:
  1. `MEDU9824101_SI.txt` (Shipping Instruction)
  2. `MEDU9824101_Draft_BL.txt` (Draft Bill of Lading)
* **What happens**:
  - Category: `BL_COMPARISON` (Confidence: 99%)
  - Status: `OK` (Verified)
  - Result: All 7 canonical fields match (Shipper, Consignee, Notify Party, POL, POD, Containers, Gross Weight).

---

### 🔴 Scenario 2: Gross Weight & Container Discrepancy (Audit Alert)
* **Folder**: [`02_discrepancy_maersk/`](./02_discrepancy_maersk/)
* **Carrier**: Maersk Line
* **Booking**: `MAEU-7731201`
* **Attachments to attach**:
  1. `MAEU7731201_SI.txt` (Specifies 10 containers, 245,000.00 KGS)
  2. `MAEU7731201_Draft_BL.txt` (Specifies 8 containers, 231,000.00 KGS)
* **What happens**:
  - Category: `BL_COMPARISON` (Confidence: 99%)
  - Status: `MISMATCH` (Discrepancy)
  - Defect Fields: `gross_weight_kg`, `container_count`
  - Automated Carrier Auto-Amendment draft generated.

---

### 🔵 Scenario 3: Operational Billing / Invoice Query (NLP Intent Triage)
* **Folder**: [`03_invoice_query/`](./03_invoice_query/)
* **Sender**: Transocean Logistics
* **Booking**: `INV-88391`
* **Attachments to attach**: None or `Invoice_INV88391.txt`
* **What happens**:
  - Category: `INVOICE_QUERY` (Confidence: 95%)
  - Status: `Filed` (Filed automatically without triggering document reconciliation).

---

## How to Send During a Live Demo

1. **Start the live watcher** in your terminal:
   ```powershell
   .\venv\Scripts\python.exe gmail_watcher.py --watch --interval 15 --trigger-pipeline
   ```
2. Open your email client (Gmail, Outlook, phone mail) and compose a new email:
   - **To**: Your demo Gmail address.
   - **Subject & Body**: Copy from `email_template.txt` inside the scenario folder.
   - **Attachments**: Drag and drop the `.txt` attachments from the scenario folder.
3. Click **Send**!
4. Within 15 seconds, your terminal will log the arrival, decode attachments, and run the pipeline.
5. In the Web UI (`http://localhost:5173`), switch to the **Gmail Live Inbox** dataset to see the result live!
