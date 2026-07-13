export function renderAttributeSort(scene){
  const props = scene.props || {};
  const items = props.items || [
    {key:'red-pocket', label:'红色口袋服', color:'red', pocket:true},
    {key:'red-plain', label:'红色外套', color:'red', pocket:false},
    {key:'blue-pocket', label:'蓝色口袋服', color:'blue', pocket:true},
    {key:'yellow-plain', label:'黄色雨衣', color:'yellow', pocket:false},
    {key:'green-pocket', label:'绿色口袋服', color:'green', pocket:true},
    {key:'red-stripe', label:'红色条纹衣', color:'red', pocket:false},
  ];
  const clues = props.clues || [
    {key:'red', label:'红色', attr:'color', value:'red'},
    {key:'pocket', label:'有口袋', attr:'pocket', value:true},
  ];
  const target = props.target || {color:'red', pocket:true};
  const active = new Set(props.activeClues || []);
  const stage = document.createElement('div');
  stage.className = 'attribute-stage';
  const cluePanel = document.createElement('div');
  cluePanel.className = 'clue-panel';
  const clueTitle = document.createElement('div');
  clueTitle.className = 'clue-title';
  clueTitle.textContent = props.clueTitle || '订单线索';
  const result = document.createElement('div');
  result.className = 'clue-result';
  const grid = document.createElement('div');
  grid.className = 'clothing-grid';

  function matchesClue(item, clue){
    return item[clue.attr] === clue.value;
  }

  function matchesTarget(item){
    return Object.keys(target).every(key => item[key] === target[key]);
  }

  function draw(){
    grid.innerHTML = '';
    const activeClues = clues.filter(clue => active.has(clue.key));
    const matched = items.filter(item => activeClues.every(clue => matchesClue(item, clue)));
    items.forEach(item => {
      const card = document.createElement('div');
      const isMatch = activeClues.length > 0 && activeClues.every(clue => matchesClue(item, clue));
      const isTarget = activeClues.length === clues.length && matchesTarget(item);
      card.className = 'cloth-card' + (isMatch ? ' match' : '') + (isTarget ? ' target' : '');
      const shape = document.createElement('div');
      shape.className = 'cloth-shape ' + (item.color || 'blue');
      if (item.pocket) {
        const pocket = document.createElement('div');
        pocket.className = 'cloth-pocket';
        shape.appendChild(pocket);
      }
      const label = document.createElement('div');
      label.className = 'cloth-label';
      label.textContent = item.label || item.key;
      card.append(shape, label);
      grid.appendChild(card);
    });
    if (activeClues.length === 0) {
      result.textContent = '先点一个线索，衣服会亮起来。';
    } else if (activeClues.length < clues.length) {
      result.textContent = '找到 ' + matched.length + ' 件，再加一个线索会更准。';
    } else {
      const targetItem = items.find(matchesTarget);
      result.textContent = targetItem ? '破案！目标是：' + targetItem.label : '还没有找到完全符合的衣服。';
    }
  }

  cluePanel.appendChild(clueTitle);
  clues.forEach(clue => {
    const btn = document.createElement('button');
    btn.className = 'clue-chip' + (active.has(clue.key) ? ' active' : '');
    btn.type = 'button';
    btn.textContent = clue.label;
    btn.onclick = () => {
      if (active.has(clue.key)) active.delete(clue.key);
      else active.add(clue.key);
      btn.classList.toggle('active', active.has(clue.key));
      draw();
    };
    cluePanel.appendChild(btn);
  });
  cluePanel.appendChild(result);
  stage.append(cluePanel, grid);
  draw();
  return [stage];
}
