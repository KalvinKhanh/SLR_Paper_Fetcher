from bs4 import BeautifulSoup
with open('sd_content.html', 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

for b in soup.find_all(['button', 'a']):
    text = b.get_text(strip=True)
    if any(k in text.lower() for k in ['access', 'organization', 'pdf', 'purchase']):
        print(f"{b.name} | class={b.get('class')} | text='{text}' | href={b.get('href')}")
