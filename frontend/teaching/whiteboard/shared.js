export function sceneButton(text, onClick){
  const btn = document.createElement('button');
  btn.className = 'scene-action';
  btn.type = 'button';
  btn.textContent = text;
  btn.onclick = onClick;
  return btn;
}
