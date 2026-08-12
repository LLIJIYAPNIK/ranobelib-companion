// Reading-speed test (PR 77): a "Скорость чтения" subsection inside "Чтение с помощью
// тапа" settings. The visitor reads a sample paragraph at a comfortable pace for exactly
// 60 seconds, then clicks the word they stopped at - the number of words up to and
// including that one *is* the words-per-minute rate, since the window is exactly one
// minute long. Saved into the same readerSettings localStorage key PR 30/63-65 already
// use (readingSpeedWpm), for PR 78's manual field and PR 79's tempo animations to read.
(() => {
  const STORAGE_KEY = "readerSettings";
  const TEST_DURATION_MS = 60_000;

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
  if (!root) return;

  const startBtn = root.querySelector('[data-role="reading-speed-start"]');
  const statusEl = root.querySelector('[data-role="reading-speed-status"]');
  const sampleEl = root.querySelector('[data-role="reading-speed-sample"]');
  const valueEl = root.querySelector('[data-role="reading-speed-value"]');
  if (!startBtn || !statusEl || !sampleEl || !valueEl) return;

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

  let timer = null;
  let awaitingClick = false;

  function startTest() {
    if (timer) clearTimeout(timer);
    buildSample();
    sampleEl.hidden = false;
    statusEl.hidden = false;
    startBtn.disabled = true;
    awaitingClick = false;
    statusEl.textContent =
      "Читайте в комфортном темпе. Через 60 секунд кликните слово, на котором остановились.";

    timer = setTimeout(() => {
      awaitingClick = true;
      statusEl.textContent = "Время вышло! Кликните слово, на котором вы остановились.";
    }, TEST_DURATION_MS);
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
    startBtn.disabled = false;
    statusEl.textContent = `Готово: ${wpm} слов/мин.`;
  });

  startBtn.addEventListener("click", startTest);

  renderCurrentValue();
})();
