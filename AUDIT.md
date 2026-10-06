# Audit SLR Paper Fetcher — 06/10/2026

## Cập nhật kiến trúc chờ CAPTCHA thủ công

### Nguyên nhân xác định trong code

- Bản trước dùng deadline 180 giây, rồi trả lỗi và dọn toàn bộ tab của bài.
  Người dùng có thể mất tab đang xác minh và SessionStorage dù context còn sống.
- Detector coi một lần biến mất của challenge là đã xong; chưa chờ bài ổn định.
  DOM đang chuyển hướng cũng có thể bị nhầm với kết quả xác minh.
- Nhánh phục hồi Edge đổi sang Chromium sau browser đóng, kể cả nếu trước đó
  vừa gặp challenge; chuyển cookie không tái tạo toàn bộ phiên browser.
- Một số PDF ScienceDirect được gọi bằng HTTP client của Playwright thay vì
  network stack của trang đang mở, và có thử thêm ba endpoint PDF suy đoán.
- Không thấy `new_context`, xóa cookie/profile, UA ngẫu nhiên, stealth, sửa
  fingerprint hoặc proxy rotation trong luồng browser. Browser đã dùng persistent
  context, nhưng việc dọn tab/chờ xác minh/đổi engine chưa phù hợp.

### Thay đổi hiện tại

- Module `browser_verification.py` phân biệt challenge, loading, ready và closed.
  Chỉ quan sát DOM; yêu cầu trang không còn challenge và ổn định 1.5 giây.
- Với xác minh thủ công, mặc định chờ không giới hạn và giữ nguyên context/tab.
  UI hướng dẫn xác minh trong cửa sổ đã mở, không đóng browser, tự tiếp tục khi xong.
- Tab đang chờ và tab bài đã xác minh không bị job cleanup đóng. DOI tiếp theo
  cùng publisher dùng lại tab đã xác minh; retry DOI đang chờ không gọi `goto`.
- Ưu tiên Chrome đã cài cho đăng nhập tương tác, profile riêng theo channel.
  Không đổi engine/restart/retry tự động sau khi phiên đã gặp challenge.
- Guard kiểm tra challenge trước click, điều hướng và yêu cầu PDF. ScienceDirect
  và phiên từng gặp challenge lấy PDF qua network stack của browser; bỏ endpoint
  ScienceDirect suy đoán. Browser dùng UA mặc định; HTTP nguồn công khai dùng tên app.
- Snapshot chỉ nhập login cookie của các domain thư viện tin cậy, loại cookie
  challenge ngay cả trên domain thư viện. Bỏ script nhập localStorage.
  Trạng thái publisher/challenge chỉ nằm trong persistent profile của chính engine.
- Khi đóng ứng dụng, báo dừng cho worker trước khi đợi job, tránh chờ CAPTCHA vô hạn.

Kiến trúc này loại bỏ các thao tác gây mất phiên ở phía ứng dụng. Không có bảo đảm
Cloudflare chỉ hỏi một lần: quyết định challenge còn thuộc về nhà xuất bản/Cloudflare.
Xem [Playwright persistent context](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)
và [Cloudflare challenge loops](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/resolve-challenge/).

### Kiểm chứng luồng CAPTCHA

- Bộ kiểm tra hiện có **38/38 đạt** trên các trang fixture cục bộ/intercepted.
- Chạy thêm bài kiểm tra challenge bằng Google Chrome đã cài: **1/1 đạt**.
- Challenge thoáng biến mất rồi xuất hiện lại: ứng dụng chưa tiếp tục trước khi
  article thật của fixture ổn định; không click checkbox/challenge.
- Hết deadline thử: tab/context vẫn mở. Hoàn tất xác minh trong fixture rồi resume
  cùng DOI: không điều hướng lại trang bài và PDF được lưu đúng.
- DOI tiếp theo dùng cùng tab/context, cookie và SessionStorage còn nguyên;
  thao tác phục hồi không gọi context.close hoặc đổi engine sau challenge.
- Chờ không deadline có thể dừng bằng stop event, không tự đóng tab trong waiter.
- Python compile, JavaScript syntax và diff whitespace đã kiểm tra.

Chưa xác minh trực tiếp Cloudflare trên ScienceDirect bằng tài khoản thật ở lần
cập nhật này. Các fixture chứng minh hành vi phía ứng dụng, không chứng minh nhà
xuất bản sẽ chấp nhận một lần xác minh cho mọi bài hoặc mọi IP.

Các phần dưới là báo cáo các phiên bản trước cập nhật CAPTCHA này.

## Cập nhật theo yêu cầu bỏ API Elsevier

Luồng tải hiện tại đã ngừng gọi API Elsevier. Các kết quả 401/403 bên dưới là
ghi nhận của lần audit trước, không phải một bước trong luồng tải mới.

- Bài có link PDF OA hoặc link kho tác giả được tải ngay.
- Bài còn lại ưu tiên tài khoản VNU/OpenAthens trên web, chọn tổ chức và View PDF.
- Nếu chưa lấy được PDF qua phiên web, chuyển sang tìm các nguồn OA.
- Dùng profile trình duyệt riêng để giữ cookie/local storage/IndexedDB qua lần
  khởi động lại; tự nhập snapshot phiên cũ và khôi phục cookie SSO còn hiệu lực.
- Theo cửa sổ đăng nhập mới, tránh gửi lại cùng form đăng nhập nhiều lần.
- Nhận PDF từ response, attachment, URL có chữ ký hoặc blob viewer đã được cấp quyền.
- Kiểm tra cooldown tại worker để các bài đang xếp hàng không tiếp tục lặp CAPTCHA.
- Mặc định dùng Chromium đi kèm Playwright. Nếu cấu hình Edge và browser tự đóng,
  thử lại một lần với Chromium, nhập snapshot phiên VNU đã lưu.
- Bỏ qua target ẩn `edge://downloads-hub/` khi dọn tab; target này khiến thao tác
  đóng tab đồng bộ bị kẹt trong fixture Edge. Gỡ listener PDF trước khi đóng tab bài.
- Endpoint tra cứu API Elsevier cũ trả 410; endpoint trạng thái báo rõ API tải đã tắt.

Các thay đổi này sử dụng quyền web của tài khoản. Không suy việc API bị từ chối
là tài khoản thư viện không có quyền đọc bài. CAPTCHA/MFA vẫn cần người dùng xác minh.

### Kiểm chứng bản cập nhật

- Bộ kiểm tra hiện có: **38/38 đạt**. Fixture trình duyệt tải ba PDF, chỉ đăng
  nhập một lần, giữ phiên qua đóng/mở lại browser; kiểm tra cả PDF blob.
- Chạy lại kiểm tra scan DOI hợp lệ/không hợp lệ 30 lần: **30/30 đạt** sau khi
  khóa khởi tạo catalog, tránh các worker cùng tạo schema/WAL trên cache miss.
- Chạy thêm fixture trên Edge: **1/1 đạt**, kể cả phục hồi tải sau khi browser
  đóng bằng Chromium và snapshot đăng nhập. Toàn bộ SSO dùng tài khoản giả và
  route fixture, không gửi thông tin tài khoản thật tới các trang thử.
- Compile Python, kiểm tra cú pháp JavaScript và `git diff --check` đạt.
- Server đang chạy cổng 8543: health 200; trạng thái API tải `false`, chiến lược
  `institutional_browser`; endpoint tìm Elsevier cũ trả 410.

Đây là kiểm chứng luồng ứng dụng trên fixture. Chưa xác nhận một lần tải trực tiếp
ScienceDirect bằng phiên VNU mới của tài khoản thật trong bản cập nhật này. Bản PDF
MAFSA tải thật từ kho tác giả bên dưới vẫn là kết quả kiểm chứng của lần audit trước.

Phần audit bên dưới mô tả phiên bản trước cập nhật này và kết quả kiểm chứng khi đó.

## Phạm vi và cấu trúc

Đã rà soát ứng dụng hiện tại: `app.py`, `downloader_engine.py`, các module
nguồn tải/API/trình duyệt, cấu hình, frontend, dependencies và script chạy.
Giữ FastAPI + Requests + Playwright + giao diện hiện có; bổ sung SQLite bằng
thư viện chuẩn Python và worker trình duyệt riêng. Không tạo project mới.

Không sửa nội dung `.env` của người dùng. Không xuất API key, mật khẩu hoặc
cookie ra báo cáo. Không đăng nhập vào Sci-Hub hoặc giải/bypass CAPTCHA/MFA.

## Các lỗi tìm thấy và đã xử lý

| Vấn đề | Thay đổi |
| --- | --- |
| `10.bad/x` vượt qua kiểm tra `startswith('10.')` | Chuẩn hóa DOI, kiểm tra đầy đủ, giữ dòng `INVALID_DOI`, loại DOI trùng |
| Suy nhà xuất bản từ prefix DOI | Theo redirect DOI, ghi URL/hostname/provider; chọn API dựa trên host |
| Mỗi bài khởi tạo và đóng browser/context | Một worker sở hữu Playwright trên cùng thread; tái sử dụng context/cookie |
| Listener PDF có thể tồn tại khi tái sử dụng context | Gỡ listener từng bài; đóng tab của bài sau mỗi lần xử lý |
| Trang chuyển hướng/đóng gây lỗi Playwright | Chờ DOM ổn định, kiểm tra trang đóng, bắt lỗi và trả trạng thái có thể thử lại |
| Coi chuyển khỏi trang login là đã xác thực | Kiểm tra marker đăng nhập/quyền toàn văn; nhận diện form login/MFA riêng |
| Không phân biệt xác minh và lỗi quyền | Trạng thái CAPTCHA/MFA/login/session hết hạn; chờ người dùng trên browser hiện hình |
| File tồn tại được coi là thành công bất kể nội dung | Kiểm tra header/EOF/HTML, xác nhận quan hệ DOI–file trong catalog |
| Báo 100% hoặc thành công trước khi hoàn tất lưu | Chỉ gửi 100 sau khi kiểm tra file và ghi trạng thái `DOWNLOADED` |
| Dòng thanh tiến độ bị lệch khi bỏ qua bài đã tải | Ánh xạ index batch về đúng index dòng gốc |
| Bộ đếm không tính đúng bài đã có và OA chuyển VNU | Tách số xử lý, thành công, thất bại; cập nhật tổng từng luồng |
| OA tải thất bại vẫn gửi 100% | Bỏ tín hiệu thành công giả; chuyển sang bước tổ chức |
| Đồng bộ PDF mới nhất từ Downloads vào bài bất kỳ | Bỏ tự đồng bộ theo thời gian; import phải chỉ rõ tên nguồn và DOI, không xóa bản gốc |
| Tên file và endpoint RIS cho phép thoát thư mục | Kiểm tra đường dẫn và tên file, kể cả trên Windows |
| Dữ liệu tài khoản viết cứng trong HTML | Chỉ hiển thị trạng thái đã/chưa cấu hình; credential lấy ở backend |
| Metadata/filename nhúng thẳng vào HTML/onclick | Dùng DOM `textContent`, closure và kiểm tra scheme URL |
| Mất trạng thái khi tải lại trang | SQLite catalog + lịch sử trạng thái + nút khôi phục danh sách |
| Có thể ghi đè PDF của DOI khác | Khóa theo DOI/đường dẫn, lưu quyền sở hữu tên file, giữ PDF chưa có catalog |
| Đóng tab frontend gây gián đoạn ghi file | Job tải tiếp tục trong server, kết quả lưu vào catalog; stream có heartbeat |
| Endpoint tải trực tiếp chặn event loop | Chuyển việc tải đồng bộ sang worker thread |
| Thiếu dependencies để chạy Playwright/XML/XLS | Bổ sung Playwright, lxml, xlrd; script cài browser và dừng khi cài đặt lỗi |
| ScienceDirect Query API cũ không còn hoạt động | Dùng Article Metadata API; Article Retrieval API cho PDF |

## Luồng hiện tại

DOI → resolve host → nguồn Open Access/kho tác giả → Unpaywall/Semantic
Scholar/OpenAlex/PMC OA/arXiv → API Elsevier nếu host phù hợp → VNU/OpenAthens.

Tối đa hai bài đang lấy file OA; một worker trình duyệt xử lý VNU, không chặn
luồng OA. Các nguồn công khai có khoảng nghỉ chung theo host; HTTP 429 áp dụng
`Retry-After`. Host gặp CAPTCHA quá thời gian chờ được tạm nghỉ mười phút.

Các trạng thái được lưu gồm `PENDING`, `PROCESSING`, `OPEN_ACCESS`,
`LOGIN_REQUIRED`, `AUTHENTICATED`, `SESSION_EXPIRED`, `INSTITUTION_ACCESS`,
`NO_ACCESS`, `MFA_REQUIRED`, `CAPTCHA_REQUIRED`, `INVALID_DOI`, `INVALID_PDF`,
`RESOLUTION_FAILED`, `DOWNLOADED`, `FAILED`, `CANCELLED`.

Marker đăng nhập và marker quyền đọc là hai kiểm tra riêng. Việc đọc được một
bài OA không được dùng để khẳng định đã đăng nhập tổ chức.

## Kiểm chứng

Đã chạy ứng dụng thực bằng Uvicorn trên `127.0.0.1:18543`, tách khỏi cổng
ứng dụng người dùng. Kết quả HTTP:

- `/api/health`: 200.
- Scan một DOI thật, bản DOI trùng viết hoa và một DOI sai: hai dòng kết quả,
  một DOI trùng được loại, dòng sai mang `INVALID_DOI`.
- Tải lại bài đã lưu: thành công, không tải mạng lại, số đếm cuối 1/1.
- Tên RIS `../README.md`: 400; trước sửa đã trả 200.
- Batch `items: [1]`: 400 trước khi mở stream.
- Kiểm tra cú pháp JavaScript bằng `node --check`: đạt.

Đã chạy 38 kiểm tra tự động thành công, bao gồm trình duyệt Edge thật với
fixture cục bộ: hai bài chỉ đăng nhập một lần, cookie dùng để lấy PDF, listener
không gán nhầm file, metadata không thực thi HTML, progress đúng dòng sau khi
lọc batch, nhận diện MFA và chờ xác minh mà không bấm challenge. Các kiểm tra
còn bao phủ API 401/403/429, PDF HTML/thiếu, CSV/XLSX, DOI, tên file, bộ đếm,
giới hạn hai bài OA và khôi phục file đã lưu trước khi process bị dừng.

```powershell
python -m unittest discover -s tests -v
node --check downloads/audit_frontend.js
python -m compileall -q app.py downloader_engine.py browser_pdf.py browser_session.py paper_io.py paper_store.py doi_resolver.py institutional_auth.py http_policy.py
```

### Tải mạng thật

Bài **MAFSA: A multi-layer asynchronous federated learning with
staleness-awareness in edge computing**, DOI `10.1016/j.engappai.2026.114369`:

- Resolver trả host `linkinghub.elsevier.com`, provider Elsevier.
- Luồng tự động tải từ [kho tác giả tại SUNY New Paltz](https://www.cs.newpaltz.edu/~lik/publications/Shiwen-Zhang-EAAI-2026.pdf).
- File `downloads/MAFSA_audit_live.pdf`: 3.291.599 byte, kiểm tra PDF đạt,
  catalog ghi `DOWNLOADED`, method `public_copy`.
- Tra metadata qua key hiện tại: HTTP 401.
- Thử tải PDF qua Article Retrieval API: HTTP 403; không lưu trang lỗi thành PDF.

## Giới hạn còn lại và cách sử dụng

1. Chưa có bằng chứng quyền API Elsevier đã được cấp cho key/IP/token hiện
   tại. Cần kiểm tra key, quyền endpoint và quyền tổ chức với Elsevier/thư viện.
   Đăng nhập web VNU không tự cấp quyền API. Xem [tài liệu xác thực Elsevier](https://dev.elsevier.com/tecdoc_api_authentication.html).
2. Kiểm tra SSO tự động dùng fixture, không khẳng định đã tải thành công qua
   tài khoản VNU trên mọi nhà xuất bản thực hoặc trên toàn bộ danh sách 197 bài.
   Quyền thuê bao và cấu trúc trang từng nhà xuất bản vẫn quyết định kết quả.
3. CAPTCHA/MFA cần người dùng hoàn tất trong browser hiện hình. Quá thời gian
   chờ, trạng thái được lưu và còn link mở bài. Không hứa tải mọi bài bất chấp quyền.
4. arXiv có thể cung cấp bản preprint. Chỉ nhận DOI khớp hoặc tiêu đề chuẩn hóa
   khớp và không có DOI khác. PMC chỉ dùng API định danh/OA và link PDF OA do API
   cung cấp; không vượt qua thời gian embargo. [PMC ID Converter](https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/), [PMC OA Service](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/).
5. Kiểm tra PDF hiện là kiểm tra vận chuyển/header/EOF; chưa dùng parser PDF
   để phân tích toàn bộ cấu trúc hoặc so nội dung bài báo.
6. `.env` đã được Git theo dõi từ trước. Thêm `.gitignore` không tự bỏ theo dõi
   file này. Audit không tạo commit; cần bỏ theo dõi secret trước khi công bố repo.

Khởi động lại bằng `run_app.bat` hoặc `python app.py`, mở lại trang, bấm
**Khôi phục danh sách** để xem trạng thái lưu và tiếp tục tải các bài chưa xong.
Giữ riêng `.env`, `.vnu-browser-state.json`, `data/` và các PDF đã tải.
