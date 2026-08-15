// Reading-speed test (PR 77) plus manual entry (PR 78): a "Скорость чтения" subsection
// inside "Чтение с помощью тапа" settings. The visitor reads a sample paragraph at a
// comfortable pace for exactly 60 seconds, then clicks the word they stopped at - the
// number of words up to and including that one *is* the words-per-minute rate, since the
// window is exactly one minute long. The test isn't the only way in, though - a plain
// number field lets a visitor set or correct the value directly, for anyone who'd rather
// not take the test at all. Both write the same readerSettings localStorage key PR
// 30/63-65 already use (readingSpeedWpm), for PR 79's tempo animations to read later, and
// stay in sync with each other - completing the test updates the manual field's own
// displayed value too, not just the "Текущее значение" line.
//
// PR 125: the test itself (sample text, status line, countdown) runs inside a modal -
// same fixed-overlay/backdrop-click/close-button pattern as title-quickview.js's
// .title-quickview-modal. Closing mid-test (the × or a backdrop click) has the same
// effect as letting the 60s timer run out without ever clicking a word: the attempt just
// doesn't count, nothing gets written to readingSpeedWpm. The manual field lives outside
// the modal entirely and isn't touched by any of this.
(() => {
  const STORAGE_KEY = "readerSettings";
  const TEST_DURATION_MS = 60_000;
  const MIN_WPM = 50;
  const MAX_WPM = 1000;

  // ~550 words - comfortably longer than even a very fast reader (500+ wpm) would get
  // through in 60 seconds, so running out of text mid-test isn't a realistic concern.
  const SAMPLE_TEXT = `
    Тёмный лес тянулся вдоль старой дороги, и путник шёл вперёд, не оглядываясь назад.
    Ветер шевелил кроны деревьев, а где-то далеко ухала сова. Он помнил каждое слово из
    того письма, которое получил перед отъездом, и снова и снова прокручивал его в
    голове, пытаясь понять, что скрывается между строк. Дорога петляла между холмами,
    то поднимаясь вверх, то спускаясь в узкие овраги, поросшие густым кустарником.
    Иногда навстречу попадались редкие путники, но никто из них не задерживался для
    разговора - все спешили укрыться от надвигающейся непогоды. Небо над лесом медленно
    затягивалось тучами, и первые капли дождя упали на пыльную тропу задолго до того,
    как он успел найти укрытие. В деревне, куда он направлялся, его никто не ждал, но
    именно там, по слухам, жил человек, знавший правду о случившемся много лет назад.
    Всю дорогу он вспоминал детство, проведённое в этих местах, старый дом на окраине
    и голос матери, зовущий его домой перед закатом. Время шло медленно, будто нарочно
    растягивая каждую минуту, и путник начал сомневаться, правильно ли он выбрал путь.
    Тропа вывела его к небольшой речке, через которую был перекинут ветхий деревянный
    мост. Доски скрипели под ногами, а вода внизу казалась почти чёрной в сумеречном
    свете. На другом берегу дорога снова уходила в лес, но теперь деревья стояли
    реже, и сквозь них уже виднелись огоньки далёкой деревни. Он ускорил шаг, чувствуя,
    как усталость постепенно уступает место нетерпению. Каждый шорох в кустах заставлял
    его вздрагивать, хотя разумом он понимал, что бояться, скорее всего, нечего. Когда
    наконец он вышел на околицу, дождь уже полил по-настоящему, и путник побежал к
    первому дому с горящим окном, надеясь застать хозяев дома и услышать долгожданный
    ответ на вопрос, который мучил его всю дорогу. Дверь открыла пожилая женщина с
    керосиновой лампой в руке и, увидев на пороге незнакомца, не удивилась, будто ждала
    его уже давно. Она провела его в тёплую комнату, где на столе стояли хлеб и кувшин
    с молоком, и лишь потом спросила, зачем он пришёл в такую даль и в такую погоду.
    Путник рассказал ей о письме, о человеке, которого искал, и о вопросе, не дававшем
    ему покоя все эти годы. Женщина долго молчала, глядя на огонь в печи, а затем
    произнесла, что тот, о ком он спрашивает, ушёл из деревни много лет назад и не
    оставил после себя ни адреса, ни весточки. Однако она помнила его достаточно хорошо,
    чтобы рассказать всё, что знала сама, - о том, каким он был до отъезда, о его
    привычках, о людях, с которыми он общался, и о причине, по которой ему пришлось
    покинуть родные места так внезапно. Слушая её неторопливый рассказ, путник начал
    понимать, что правда, которую он искал столько лет, оказалась куда сложнее, чем он
    себе представлял, и что простого ответа здесь, возможно, никогда и не существовало.
    За окном дождь постепенно стихал, а огонь в печи разгорался всё ярче, отбрасывая
    длинные тени на бревенчатые стены. Женщина налила ему горячего чаю и, придвинув
    стул ближе, продолжила рассказ, не пропуская ни одной детали, которую считала важной.
    Она говорила о зиме, когда в деревне случился пожар, о лете, когда река вышла из
    берегов, и о том дне, когда человек, которого искал путник, впервые появился на
    пороге её дома с тем же самым выражением лица, какое она видела теперь. К утру,
    когда дождь совсем прекратился и первые лучи солнца пробились сквозь облака, путник
    знал уже достаточно, чтобы продолжить свой путь, но теперь он шёл не искать ответы,
    а нести с собой то, что успел узнать за одну долгую дождливую ночь в чужом доме.
  `;

  const root = document.querySelector('[data-role="reading-speed-test"]');
  const modal = document.querySelector('[data-role="reading-speed-modal"]');
  if (!root || !modal) return;

  const startBtn = root.querySelector('[data-role="reading-speed-start"]');
  const valueEl = root.querySelector('[data-role="reading-speed-value"]');
  const manualInput = root.querySelector('[data-role="reading-speed-manual-input"]');
  const manualDecrement = root.querySelector('[data-role="reading-speed-manual-decrement"]');
  const manualIncrement = root.querySelector('[data-role="reading-speed-manual-increment"]');

  // The test's own markup (status/sample/countdown/close button) lives inside the modal,
  // not root, now - it's a sibling of .reading-speed-test in the page, not a descendant.
  const closeBtn = modal.querySelector('[data-role="reading-speed-modal-close"]');
  const timerEl = modal.querySelector('[data-role="reading-speed-timer"]');
  const statusEl = modal.querySelector('[data-role="reading-speed-status"]');
  const sampleEl = modal.querySelector('[data-role="reading-speed-sample"]');
  if (
    !startBtn || !valueEl || !manualInput || !manualDecrement || !manualIncrement ||
    !closeBtn || !timerEl || !statusEl || !sampleEl
  ) return;

  function loadSettings() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveWpm(wpm) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...loadSettings(), readingSpeedWpm: wpm }));
  }

  function renderCurrentValue() {
    const wpm = loadSettings().readingSpeedWpm;
    valueEl.textContent = wpm ? `${wpm} слов/мин` : "не задано";
    // Keeps the manual field showing the same value the test just saved (or whatever was
    // already saved on page load) - not just the "Текущее значение" line above it.
    manualInput.value = wpm || "";
  }

  function buildSample() {
    sampleEl.innerHTML = "";
    const words = SAMPLE_TEXT.split(/\s+/).filter(Boolean);
    words.forEach((word, index) => {
      const span = document.createElement("span");
      span.className = "reading-speed-test__word";
      span.dataset.index = String(index);
      span.textContent = word;
      sampleEl.append(span, " ");
    });
  }

  let completionTimer = null;
  let tickInterval = null;
  let testEndAt = 0;
  let awaitingClick = false;

  function isOpen() {
    return modal.classList.contains("reading-speed-modal--open");
  }

  function updateTimerDisplay() {
    const remainingSeconds = Math.max(0, Math.ceil((testEndAt - Date.now()) / 1000));
    timerEl.textContent = `${remainingSeconds} с`;
  }

  function startTest() {
    buildSample();
    sampleEl.hidden = false;
    statusEl.hidden = false;
    awaitingClick = false;
    statusEl.textContent =
      "Читайте в комфортном темпе. Через 60 секунд кликните слово, на котором остановились.";

    testEndAt = Date.now() + TEST_DURATION_MS;
    updateTimerDisplay();
    timerEl.hidden = false;
    tickInterval = setInterval(updateTimerDisplay, 250);

    completionTimer = setTimeout(() => {
      awaitingClick = true;
      statusEl.textContent = "Время вышло! Кликните слово, на котором вы остановились.";
      clearInterval(tickInterval);
      tickInterval = null;
      timerEl.hidden = true;
    }, TEST_DURATION_MS);
  }

  // Tears down whatever state startTest() set up, without saving anything - the shared
  // ending for both "time ran out and the visitor never clicked a word" and "closed the
  // modal mid-test" (openModal() calling this before every fresh startTest() guards
  // against a leftover timer from a previous attempt; closeModal() is the actual abort).
  function resetTest() {
    if (completionTimer) clearTimeout(completionTimer);
    if (tickInterval) clearInterval(tickInterval);
    completionTimer = null;
    tickInterval = null;
    awaitingClick = false;
    sampleEl.hidden = true;
    statusEl.hidden = true;
    timerEl.hidden = true;
  }

  function openModal() {
    modal.classList.add("reading-speed-modal--open");
    resetTest();
    startTest();
  }

  function closeModal() {
    resetTest();
    modal.classList.remove("reading-speed-modal--open");
  }

  sampleEl.addEventListener("click", (event) => {
    if (!awaitingClick) return;
    const word = event.target.closest(".reading-speed-test__word");
    if (!word) return;

    const wpm = Number(word.dataset.index) + 1; // 1-indexed word count over exactly 60s
    saveWpm(wpm);
    renderCurrentValue();

    awaitingClick = false;
    sampleEl.hidden = true;
    statusEl.textContent = `Готово: ${wpm} слов/мин.`;
  });

  startBtn.addEventListener("click", openModal);
  closeBtn.addEventListener("click", closeModal);

  // Clicking the backdrop itself (not the panel or anything inside it) closes the modal -
  // same delegation as title-quickview.js's own overlay click handler.
  modal.addEventListener("click", (event) => {
    if (isOpen() && event.target === modal) closeModal();
  });

  // Committed on "change" (blur/Enter), not "input" on every keystroke - clamping mid-
  // typing would fight with the cursor and make the field unusable while entering a
  // number outside [MIN_WPM, MAX_WPM] one digit at a time.
  manualInput.addEventListener("change", () => {
    const parsed = Number(manualInput.value);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      // Blank/garbage input just resets the field to whatever's still saved, rather than
      // wiping out a previously-good value.
      renderCurrentValue();
      return;
    }
    const wpm = Math.min(MAX_WPM, Math.max(MIN_WPM, Math.round(parsed)));
    saveWpm(wpm);
    renderCurrentValue();
  });

  // PR 94: +/- buttons standing in for the hidden native spinner. stepUp()/stepDown()
  // read the field's own min/max/step attributes and clamp at the boundaries for us, so
  // this doesn't duplicate MIN_WPM/MAX_WPM/step logic. An empty field is just seeded with
  // the last saved value (or MIN_WPM) and left there for this click - stepUp() on a truly
  // empty field starts from the field's min regardless of what's already stored, and would
  // silently skip past that seeded value on the very first click otherwise.
  function nudge(direction, button) {
    if (!manualInput.value) {
      manualInput.value = String(loadSettings().readingSpeedWpm || MIN_WPM);
    } else if (direction > 0) {
      manualInput.stepUp();
    } else {
      manualInput.stepDown();
    }
    manualInput.dispatchEvent(new Event("change"));
    // Mouse clicks leave :focus-visible lit on the button in some browsers even though
    // the click wasn't keyboard navigation - blur it so the stepper only stays highlighted
    // for real Tab focus, not after every click.
    button.blur();
  }

  manualDecrement.addEventListener("click", () => nudge(-1, manualDecrement));
  manualIncrement.addEventListener("click", () => nudge(1, manualIncrement));

  renderCurrentValue();
})();
