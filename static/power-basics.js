(() => {
  const money = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
  const number = (value, digits = 1) =>
    new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);

  const lab = document.querySelector("#circuit-lab");
  const voltageInput = document.querySelector("#circuit-voltage");
  const resistanceInput = document.querySelector("#circuit-resistance");
  const hoursInput = document.querySelector("#operating-hours");
  const switchButton = document.querySelector("#circuit-switch");
  const switchBlade = document.querySelector("#switch-blade");
  const wire = document.querySelector("#circuit-wire");
  const electricityRate = 0.10236;
  let circuitOn = false;
  let activeChallenge = "path";

  const challengeContent = {
    path: {
      kicker: "Challenge 1",
      title: "Give current a complete path.",
      description: "Open and close the switch. Current can flow only when the source and load are connected by a complete loop.",
    },
    power: {
      kicker: "Challenge 2",
      title: "Change how much power the load uses.",
      description: "Raise the voltage or change the resistance. Ohm’s law determines current, and voltage multiplied by current determines power.",
    },
    energy: {
      kicker: "Challenge 3",
      title: "Let power continue over time.",
      description: "Power is the rate of energy use. Operating the same load for longer increases energy consumption and cost.",
    },
  };

  function circuitValues() {
    return {
      voltage: Number(voltageInput?.value || 0),
      resistance: Number(resistanceInput?.value || 1),
      hours: Number(hoursInput?.value || 0),
    };
  }

  function renderCircuit() {
    if (!lab) return;
    const { voltage, resistance, hours } = circuitValues();
    const current = circuitOn ? voltage / resistance : 0;
    const powerWatts = circuitOn ? voltage * current : 0;
    const powerKw = powerWatts / 1000;
    const energyKwh = powerKw * hours;
    const cost = energyKwh * electricityRate;

    document.querySelector("#voltage-output").textContent = `${number(voltage, 0)} V`;
    document.querySelector("#resistance-output").textContent = `${number(resistance, 0)} Ω`;
    document.querySelector("#hours-output").textContent = `${number(hours, 2)} hours`;
    document.querySelector("#diagram-voltage").textContent = `${number(voltage, 0)} V`;
    document.querySelector("#diagram-resistance").textContent = `${number(resistance, 0)} Ω`;
    document.querySelector("#current-result").textContent = `${number(current, 2)} A`;
    document.querySelector("#power-result").textContent = powerWatts >= 1000
      ? `${number(powerKw, 2)} kW`
      : `${number(powerWatts, 0)} W`;
    document.querySelector("#energy-result").textContent = `${number(energyKwh, 2)} kWh`;
    document.querySelector("#cost-result").textContent = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(cost);
    document.querySelector("#power-idea-value").innerHTML = `${number(powerKw, 2)} <small>kW</small>`;
    document.querySelector("#energy-idea-value").innerHTML = `${number(energyKwh, 2)} <small>kWh</small>`;
    document.querySelector("#energy-fill").style.width = `${Math.max(2, (hours / 24) * 100)}%`;

    lab.classList.toggle("circuit-live", circuitOn);
    wire?.classList.toggle("live", circuitOn);
    switchButton?.setAttribute("aria-pressed", String(circuitOn));
    if (switchButton) switchButton.textContent = circuitOn ? "Open the switch" : "Close the switch";
    switchBlade?.setAttribute("x2", circuitOn ? "375" : "356");
    switchBlade?.setAttribute("y2", circuitOn ? "70" : "37");
    document.querySelector("#switch-label").textContent = circuitOn ? "CLOSED SWITCH" : "OPEN SWITCH";
    lab.style.setProperty("--flow-speed", `${Math.max(.55, 2.2 - current * .06)}s`);

    const equation = document.querySelector("#live-equation");
    if (!equation) return;
    if (!circuitOn) {
      equation.innerHTML = "<strong>Switch open:</strong> without a complete path, current and power are zero.";
    } else if (activeChallenge === "path") {
      equation.innerHTML = `<strong>Switch closed:</strong> the loop is complete, so ${number(current, 2)} A flows through the load.`;
    } else if (activeChallenge === "power") {
      equation.innerHTML = `<strong>Ohm’s law:</strong> I = V ÷ R = ${number(voltage, 0)} ÷ ${number(resistance, 0)} = ${number(current, 2)} A<br><strong>Power:</strong> P = V × I = ${number(voltage, 0)} × ${number(current, 2)} = ${number(powerWatts, 0)} W`;
    } else {
      equation.innerHTML = `<strong>Energy:</strong> E = P × t = ${number(powerKw, 2)} kW × ${number(hours, 2)} h = ${number(energyKwh, 2)} kWh`;
    }
  }

  function selectChallenge(name) {
    activeChallenge = name;
    document.querySelectorAll(".challenge-tab").forEach((tab) => {
      const selected = tab.dataset.challenge === name;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
    });
    const copy = challengeContent[name];
    document.querySelector("#challenge-kicker").textContent = copy.kicker;
    document.querySelector("#challenge-title").textContent = copy.title;
    document.querySelector("#challenge-description").textContent = copy.description;
    document.querySelector("#voltage-control").hidden = name !== "power";
    document.querySelector("#resistance-control").hidden = name !== "power";
    document.querySelector("#time-control").hidden = name !== "energy";
    renderCircuit();
  }

  document.querySelectorAll(".challenge-tab").forEach((tab) => {
    tab.addEventListener("click", () => selectChallenge(tab.dataset.challenge));
  });
  [voltageInput, resistanceInput, hoursInput].forEach((input) => input?.addEventListener("input", renderCircuit));
  switchButton?.addEventListener("click", () => {
    circuitOn = !circuitOn;
    renderCircuit();
  });
  selectChallenge("path");

  const flowContent = {
    generation: {
      kicker: "Start of the physical path",
      title: "Generation",
      description: "Power plants and renewable resources convert other forms of energy into electricity. PJM coordinates which regional resources are scheduled and dispatched to help meet expected demand reliably.",
    },
    transmission: {
      kicker: "Regional high-voltage network",
      title: "Transmission",
      description: "High-voltage transmission lines move large amounts of power between generators and local utility systems. PJM monitors and coordinates this regional network, including reliability and transmission constraints.",
    },
    peco: {
      kicker: "Villanova’s local electric utility",
      title: "PECO distribution",
      description: "PECO operates the local distribution system serving the Villanova area. Its substations, distribution lines, transformers, service equipment, and meters provide the local connection through which grid electricity reaches customers.",
    },
    villanova: {
      kicker: "Campus side of the connection",
      title: "Villanova campus distribution",
      description: "After electricity reaches the campus service connection, Villanova’s electrical distribution equipment routes power toward buildings. AVATAR focuses on how that campus energy is used and how future resources could change grid consumption.",
    },
    loads: {
      kicker: "Where electricity becomes useful",
      title: "Building loads",
      description: "Lighting, heating and cooling equipment, laboratories, dining facilities, computers, pumps, and countless smaller devices convert electrical energy into light, heat, motion, and information.",
    },
  };

  document.querySelectorAll(".flow-node").forEach((node) => {
    node.addEventListener("click", () => {
      document.querySelectorAll(".flow-node").forEach((item) => item.classList.remove("active"));
      node.classList.add("active");
      const content = flowContent[node.dataset.flow];
      document.querySelector("#flow-kicker").textContent = content.kicker;
      document.querySelector("#flow-title").textContent = content.title;
      document.querySelector("#flow-description").textContent = content.description;
    });
  });

  const generators = [
    { id: "generator-base", capacity: 200, rate: 30 },
    { id: "generator-flex", capacity: 180, rate: 48 },
    { id: "generator-peak", capacity: 150, rate: 90 },
  ];

  function renderDispatch() {
    const slider = document.querySelector("#demand-slider");
    if (!slider) return;
    const demand = Number(slider.value);
    let remaining = demand;
    let committed = 0;
    let totalOutput = 0;
    let operatingCost = 0;

    generators.forEach((unit) => {
      const output = Math.min(unit.capacity, Math.max(remaining, 0));
      remaining -= output;
      totalOutput += output;
      operatingCost += output * unit.rate;
      if (output > 0) committed += 1;

      const card = document.querySelector(`#${unit.id}`);
      card?.classList.toggle("on", output > 0);
      if (card) {
        card.querySelector(".unit-status").textContent = output > 0 ? "COMMITTED" : "OFF";
        card.querySelector(".generator-output").textContent = `${number(output, 0)} MW`;
        card.querySelector(".output-fill").style.width = `${(output / unit.capacity) * 100}%`;
      }
    });

    document.querySelector("#demand-value").textContent = number(demand, 0);
    document.querySelector("#committed-result").textContent = `${committed} of 3`;
    document.querySelector("#dispatch-result").textContent = `${number(totalOutput, 0)} MW`;
    document.querySelector("#dispatch-cost").textContent = money.format(operatingCost);
  }

  document.querySelector("#demand-slider")?.addEventListener("input", renderDispatch);
  renderDispatch();

  function renderPowerFactor() {
    const slider = document.querySelector("#pf-slider");
    if (!slider) return;
    const realPower = 100;
    const powerFactor = Number(slider.value);
    const apparentPower = realPower / powerFactor;
    const reactivePower = Math.sqrt(Math.max(0, apparentPower ** 2 - realPower ** 2));
    document.querySelector("#pf-output").textContent = powerFactor.toFixed(2);
    document.querySelector("#reactive-result").textContent = `${number(reactivePower, 1)} kvar`;
    document.querySelector("#apparent-result").textContent = `${number(apparentPower, 1)} kVA`;
  }

  document.querySelector("#pf-slider")?.addEventListener("input", renderPowerFactor);
  renderPowerFactor();
})();
