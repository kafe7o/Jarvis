# Как да пуснеш Jarvis на нов компютър (Windows)

Трябват ти: Windows 10 или 11, интернет и около 10 минути. Всичко друго скриптът инсталира сам.

## Най-лесно: един ред

1. Натисни **Start**, напиши `PowerShell` и го отвори.
2. Залепи този ред и натисни Enter:

```powershell
irm https://raw.githubusercontent.com/kafe7o/Jarvis/main/How%20to%20start%20Jarvis/install-jarvis.ps1 | iex
```

Какво прави: сваля кода в `Документи\Jarvis`, инсталира Python (ако го няма), ffmpeg (за гласа), adb (за Android телефон) и браузъра, който Jarvis управлява, и накрая те пита за ключовете на български.

- Ако напише **„Python е инсталиран. Затвори този прозорец…“**: затваряш PowerShell, отваряш нов и залепяш същия ред още веднъж. Това се случва само първия път.
- Ако напише, че не може да свали (хранилището е частно): поискай ZIP-а на кода, разархивирай го така, че папката да е `Документи\Jarvis` (вътре трябва да има `install.ps1`), и пусни същия ред: тогава той прескача свалянето.

## Ключовете

Накрая тръгва `jarvis setup` и пита за ключовете. Jarvis работи с **безплатни** мозъци; един ключ стига за начало, останалите може да добавиш после в приложението (Настройки → Връзки):

| Мозък | Откъде (безплатно, без карта) | За какво |
|---|---|---|
| Gemini | aistudio.google.com/app/apikey | вижда снимки и екрана, кратки отговори |
| NVIDIA | build.nvidia.com → Get API Key | най-силните безплатни модели за истинска работа |
| OpenRouter | openrouter.ai/keys | резерва за работа |
| Groq | console.groq.com/keys | най-бързите кратки отговори |

Всичко друго (Telegram, Google, телефон, плащания) е по желание и се пропуска с Enter.

## Пускане

След инсталацията, в същия PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
jarvis shortcut
jarvis app
```

- `jarvis shortcut` слага икона **Jarvis** на работния плот и в менюто Start; после го пускаш от нея.
- `jarvis app` отваря прозореца. Първият вход с имейл и парола те прави собственик; никой друг не може да се регистрира сам.

## После

- Jarvis се обновява сам от GitHub: поглежда на всеки 5 минути и сваля само когато засече нова версия, когато не работи по твоя заявка; веднага: Настройки → Връзки → „Обнови сега“ или `jarvis update` в PowerShell.
- Личните неща (ключове, памет, акаунти, разговори) са в `C:\Users\<име>\.jarvis` и остават при обновяване.
- Спиране: `jarvis stop`. Какво е ставало: `C:\Users\<име>\.jarvis\jarvis.log`.
- Пълното описание на всичко, което може: [README.md](../README.md) в главната папка.

## Ръчно, без скрипта

```powershell
cd $HOME\Documents
Invoke-WebRequest https://github.com/kafe7o/Jarvis/archive/refs/heads/main.zip -OutFile Jarvis.zip
Expand-Archive Jarvis.zip -DestinationPath . -Force
Rename-Item Jarvis-main Jarvis
Remove-Item Jarvis.zip
cd Jarvis
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```
