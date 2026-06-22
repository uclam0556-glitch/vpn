document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const input = document.querySelector(button.dataset.copy);
  if (!input) return;
  await navigator.clipboard.writeText(input.value);
  const previous = button.textContent;
  button.textContent = "Скопировано";
  setTimeout(() => (button.textContent = previous), 1500);
});
