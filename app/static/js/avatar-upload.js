// PR 109: the round avatar preview on /settings/account is itself the file-picker
// trigger (a <label> wrapping the visually-hidden <input type="file">) and the form now
// submits itself as soon as a file is chosen, instead of requiring a separate
// "Загрузить" click - the /settings/account/avatar endpoint (PR 96) is unchanged, only
// what triggers it. A quick client-side preview via FileReader stands in for the real
// uploaded image while the (ordinary, non-AJAX) form submission and page reload happen.
(() => {
  const form = document.querySelector('[data-role="avatar-upload-form"]');
  const input = document.querySelector('[data-role="avatar-upload-input"]');
  const preview = document.querySelector('[data-role="avatar-upload-preview"]');
  if (!form || !input || !preview) return;

  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      const img = document.createElement("img");
      img.className = "avatar-img";
      img.alt = "";
      img.src = String(reader.result);
      preview.replaceChildren(img);
      form.submit();
    };
    reader.readAsDataURL(file);
  });
})();
