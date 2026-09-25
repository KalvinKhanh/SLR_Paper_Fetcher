# 📚 SLR Paper Fetcher

> **Công cụ tự động hóa thu thập và tải toàn văn (Full-Text PDF) các bài báo nghiên cứu khoa học phục vụ nghiên cứu Systematic Literature Review (SLR).**  
> Tích hợp tìm kiếm đa tầng qua kho Open Access, Academic Mirrors và cổng thư viện Đại học Quốc gia TP.HCM (VNU-HCM OpenAthens).

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-v3-38B2AC.svg)](https://tailwindcss.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🌟 Tính Năng Nổi Bật

- **Tự động nhận diện & đa dạng đầu vào:**
  - Nhập trực tiếp danh sách mã DOI / URL bài báo (mỗi dòng một mã).
  - Tải lên file **Excel (`.xlsx`, `.xls`)** hoặc **CSV (`.csv`)** – tự động nhận diện cột mã DOI, Tiêu đề bài báo và Tên file tùy chỉnh theo cấu trúc nhật ký sàng lọc SLR (`screening_log`).
- **Cơ chế tìm kiếm Full-Text đa tầng (Multi-tier Discovery):**
  1. **Unpaywall API:** Tìm kiếm bản Open Access chính thống (Gold / Green OA) từ hơn 50.000 nhà xuất bản quốc tế.
  2. **Semantic Scholar API:** Tìm kiếm bản PDF từ các hội thảo (IEEE, ACM), kho lưu trữ ArXiv, PubMed Central, preprints.
  3. **Direct Academic Mirrors:** Tự động giải quyết các bài báo bị khóa (Paywalled) từ IEEE, Elsevier/ScienceDirect, Springer, ACM để lấy link tải PDF trực tiếp mà không cần đăng nhập.
  4. **VNU-HCM OpenAthens Proxy:** Chuyển tiếp 1-click qua cổng Thư viện Trung tâm ĐHQG-HCM cho các tạp chí độc quyền.
- **Tải trực tiếp & Lưu chuẩn hóa:**
  - Tải trực tiếp file PDF về thư mục nội bộ `downloads/` của dự án với tên file chuẩn xác.
  - Hỗ trợ **"Tải tất cả file khả dụng"** chỉ với 1 click.
- **Đồng bộ thông minh từ trình duyệt (Browser Auto-Sync):**
  - Tự động bắt các file PDF vừa tải từ trình duyệt máy tính (thư mục `Downloads` của Windows), chuyển vào thư mục dự án và đổi tên file tương ứng.
- **Giao diện hiện đại & Thân thiện:**
  - Thiết kế theo phong cách hiện đại với Tailwind CSS, hiển thị rõ ràng trạng thái: *Open Access*, *Bypass Paywall*, *Đã có trong Project*.

---

## 🏗️ Cấu Trúc Thư Mục

```text
SLR_Paper_Fetcher/
├── app.py                 # FastAPI backend server & API điều hướng
├── config.py              # Quản lý cấu hình & biến môi trường
├── downloader_engine.py   # Lõi xử lý tìm kiếm đa tầng & engine tải PDF
├── requirements.txt       # Danh sách thư viện phụ thuộc
├── run_app.bat            # Script khởi chạy 1-click trên Windows
├── test_slr_papers.xlsx   # File Excel mẫu để test tải hàng loạt
├── test_slr_papers.csv    # File CSV mẫu
├── templates/
│   └── index.html         # Giao diện web ứng dụng
└── downloads/             # Thư mục lưu trữ PDF tải về (đã gitignore)
    └── .gitkeep
```

---

## 🚀 Hướng Dẫn Cài Đặt & Sử Dụng

### 1. Yêu cầu hệ thống
- **Python 3.10** trở lên.
- Hệ điều hành: Windows, macOS hoặc Linux.

### 2. Cài đặt

1. **Clone repository về máy:**
   ```bash
   git clone https://github.com/KalvinKhanh/SLR_Paper_Fetcher.git
   cd SLR_Paper_Fetcher
   ```

2. **Cài đặt các thư viện cần thiết:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Cài đặt trình duyệt Playwright (Dùng cho Bot dự phòng nếu cần):**
   ```bash
   playwright install chromium
   ```

4. **Cấu hình file môi trường (`.env`):**
   Tạo file `.env` tại thư mục gốc (hoặc chỉnh sửa file `.env` có sẵn):
   ```env
   UNPAYWALL_EMAIL=your_email@gmail.com
   VNU_LIBRARY_ID=1100002598002
   VNU_LIBRARY_PASSWORD=your_password
   APP_PORT=8543
   ```

---

### 3. Khởi chạy ứng dụng

- **Cách 1 (Nhanh nhất trên Windows):** Click đúp vào file [`run_app.bat`](run_app.bat).
- **Cách 2 (Dùng lệnh Terminal):**
  ```bash
  python app.py
  ```

Ứng dụng sẽ tự động mở trình duyệt tại địa chỉ: **`http://127.0.0.1:8543`**.

---

## 🧪 Cách Thử Nghiệm Nhanh

1. **Cách 1: Dán danh sách DOI mẫu:**
   Dán các mã DOI sau vào ô văn bản bên trái rồi bấm **"Kiểm tra & Tìm Link"**:
   ```text
   10.1371/journal.pone.0259837
   10.1109/MC.2018.2888771
   10.1109/ICSE48619.2023.00030
   10.1016/j.future.2018.04.035
   10.1145/3377811.3380327
   ```

2. **Cách 2: Sử dụng File Excel test mẫu:**
   - Tại khung **"Tải lên file Excel / CSV"**, chọn file [`test_slr_papers.xlsx`](test_slr_papers.xlsx) có sẵn trong project.
   - Bấm **"Xử lý file Excel"**.
   - Bấm **"Tải tất cả file khả dụng"** để kiểm tra tính năng tải đồng loạt 5 bài báo về thư mục `downloads/`.

---

## 🛠️ Công Nghệ Sử Dụng

- **Backend:** [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/)
- **Xử lý dữ liệu:** [Pandas](https://pandas.pydata.org/), [OpenPyXL](https://openpyxl.readthedocs.io/)
- **Crawler & Mạng:** [Requests](https://requests.readthedocs.io/), [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/), [Playwright](https://playwright.dev/python/)
- **Frontend:** HTML5, [Tailwind CSS](https://tailwindcss.com/), Vanilla JavaScript, Jinja2 Templates

---

## 📄 Bản Quyền & Tuyên Bố Miễn Trừ Trách Nhiệm

Dự án được xây dựng nhằm phục vụ mục đích học tập, nghiên cứu khoa học và thực hiện đề tài tốt nghiệp / môn học SWP391 tại Trường Đại học FPT (FPT University).  
Mọi tài liệu tải về cần tuân thủ quy định về sở hữu trí tuệ và quyền tác giả của từng nhà xuất bản.
