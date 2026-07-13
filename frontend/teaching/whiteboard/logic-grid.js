import { sceneButton } from './shared.js';

export function renderLogicGrid(scene) {
  const props = scene.props || {};
  const mode = props.mode || 'truth_table'; // 'truth_table' | 'equation' | 'chain'
  const rows = props.rows || [];
  const cols = props.cols || [];
  const clues = props.clues || [];
  const answerGrid = props.answerGrid || null;

  const stage = document.createElement('div');
  stage.className = 'logic-stage';

  // Table — only when rows + cols provided via props
  if (rows.length && cols.length) {
    const table = document.createElement('div');
    table.className = 'logic-table';

    const headerRow = document.createElement('div');
    headerRow.className = 'logic-row header';
    const corner = document.createElement('div');
    corner.className = 'logic-cell corner';
    corner.textContent = props.cornerLabel || (mode === 'chain' ? '步骤' : '');
    headerRow.appendChild(corner);
    cols.forEach(col => {
      const h = document.createElement('div');
      h.className = 'logic-cell header-cell';
      h.textContent = col;
      headerRow.appendChild(h);
    });
    table.appendChild(headerRow);

    const cellStates = rows.map(() => cols.map(() => 'empty'));

    function drawTable() {
      table.querySelectorAll('.logic-row:not(.header)').forEach(r => r.remove());
      rows.forEach((row, ri) => {
        const rowEl = document.createElement('div');
        rowEl.className = 'logic-row';
        const label = document.createElement('div');
        label.className = 'logic-cell row-label';
        label.textContent = row;
        rowEl.appendChild(label);
        cols.forEach((col, ci) => {
          const cell = document.createElement('div');
          cell.className = 'logic-cell data-cell';
          const state = cellStates[ri][ci];
          if (state === 'yes') {
            cell.textContent = '✓';
            cell.classList.add('yes');
          } else if (state === 'no') {
            cell.textContent = '✗';
            cell.classList.add('no');
          } else {
            cell.textContent = '?';
            cell.classList.add('empty-state');
          }
          cell.onclick = () => {
            if (state === 'empty') cellStates[ri][ci] = 'yes';
            else if (state === 'yes') cellStates[ri][ci] = 'no';
            else cellStates[ri][ci] = 'empty';
            drawTable();
          };
          rowEl.appendChild(cell);
        });
        table.appendChild(rowEl);
      });
    }

    drawTable();
    stage.appendChild(table);

    const tableActions = document.createElement('div');
    tableActions.className = 'scene-actions';
    tableActions.append(
      sceneButton('重置表格', () => {
        rows.forEach((_, ri) => cols.forEach((_, ci) => { cellStates[ri][ci] = 'empty'; }));
        drawTable();
      })
    );
    if (answerGrid) {
      tableActions.append(
        sceneButton('查看答案', () => {
          rows.forEach((_, ri) => cols.forEach((_, ci) => {
            cellStates[ri][ci] = answerGrid[ri][ci];
          }));
          drawTable();
        })
      );
    }
    stage.appendChild(tableActions);
  }

  // Equation mode — only when equations provided via props
  if (mode === 'equation' && props.equations && props.equations.length) {
    const eqSection = document.createElement('div');
    eqSection.className = 'eq-section';
    const eqDisplay = document.createElement('div');
    eqDisplay.className = 'eq-display';
    props.equations.forEach(eq => {
      const line = document.createElement('div');
      line.className = 'eq-line';
      line.textContent = eq;
      eqDisplay.appendChild(line);
    });
    eqSection.appendChild(eqDisplay);

    const solveBtn = sceneButton(props.solveLabel || '求解', () => {
      if (props.solution) {
        const result = document.createElement('div');
        result.className = 'eq-result';
        result.textContent = props.solution;
        eqSection.appendChild(result);
      }
    });
    eqSection.appendChild(solveBtn);
    stage.appendChild(eqSection);
  }

  // Chain mode — only when chainSteps provided via props
  if (mode === 'chain' && props.chainSteps && props.chainSteps.length) {
    const chainSection = document.createElement('div');
    chainSection.className = 'chain-section';
    const chainDisplay = document.createElement('div');
    chainDisplay.className = 'chain-display';
    props.chainSteps.forEach(step => {
      const s = document.createElement('div');
      s.className = 'chain-step';
      s.textContent = step;
      chainDisplay.appendChild(s);
    });
    chainSection.appendChild(chainDisplay);

    const revealBtn = sceneButton(props.revealLabel || '查看答案', () => {
      if (props.chainAnswer) {
        const answer = document.createElement('div');
        answer.className = 'chain-answer';
        answer.textContent = props.chainAnswer;
        chainSection.appendChild(answer);
      }
    });
    chainSection.appendChild(revealBtn);
    stage.appendChild(chainSection);
  }

  // Clue display — only when clues provided via props
  if (clues.length) {
    const cluePanel = document.createElement('div');
    cluePanel.className = 'clue-list';
    clues.forEach((clue, idx) => {
      const chip = document.createElement('button');
      chip.className = 'clue-hint';
      chip.type = 'button';
      chip.textContent = (props.clueLabel || '线索') + (idx + 1) + ': ' + clue;
      chip.onclick = () => { chip.classList.toggle('revealed'); };
      cluePanel.appendChild(chip);
    });
    stage.appendChild(cluePanel);
  }

  return [stage];
}
