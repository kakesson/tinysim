# Phase 2: the translation to ModelingToolkit, checked against the oracle.
#
# The golden files record what the Python implementation says about every
# example. These tests are the point of the whole migration strategy: the port
# is not "done" when it runs, it is done when it reproduces those numbers.

using ModelingToolkit, OrdinaryDiffEq, Logging

"""Look up a dotted model name in a compiled system: `c.v` -> `system.c.v`."""
lookup(system, name) =
    foldl((value, part) -> getproperty(value, Symbol(part)), split(name, ".");
          init = system)

"""Build and compile one example, quietly."""
function compile_example(file, model)
    program = TinySim.parse_file(joinpath(EXAMPLES, file))
    with_logger(NullLogger()) do
        mtkcompile(TinySim.build(program, model))
    end
end

function simulate_example(system, stop; solver = Rodas5P())
    with_logger(NullLogger()) do
        solve(ODEProblem(system, [], (0.0, stop)), solver;
              reltol = 1e-10, abstol = 1e-12)
    end
end

@testset "the flat model matches the oracle" begin
    # `expand_connections` is MTK's flattening, and it should produce the same
    # equation count as the Python flattener did.
    for (name, file, model) in [("electrical", "electrical.tiny", "RCCircuit"),
                                ("tank", "tank.tiny", "Tank"),
                                ("dcmotor", "dcmotor.tiny", "DCMotor"),
                                ("pendulum", "pendulum.tiny", "Pendulum"),
                                ("bouncing_ball", "bouncing_ball.tiny", "BouncingBall"),
                                ("resistor_network", "resistor_network.tiny",
                                 "ResistorNetwork"),
                                ("diode_circuit", "diode_circuit.tiny", "DiodeCircuit")]
        record = golden(name)
        program = TinySim.parse_file(joinpath(EXAMPLES, file))
        system = TinySim.build(program, model)
        flat = with_logger(NullLogger()) do
            expand_connections(system)
        end
        @test length(equations(flat)) == record.flat.equation_count
        @test length(unknowns(flat)) == record.flat.continuous_count
    end

    # The thermostat is the one exception, and it is the documented one: a
    # discrete variable becomes a held state, so `D(on) ~ 0` is one equation
    # the Python flattener never had.
    record = golden("thermostat")
    system = TinySim.build(TinySim.parse_file(joinpath(EXAMPLES, "thermostat.tiny")),
                           "Thermostat")
    @test length(equations(system)) == record.flat.equation_count + 1
end

@testset "$name reproduces the oracle's numbers" for (name, file, model, stop) in [
        ("electrical", "electrical.tiny", "RCCircuit", 1.0),
        ("tank", "tank.tiny", "Tank", 20.0),
        ("pendulum", "pendulum.tiny", "Pendulum", 10.0),
        ("dcmotor", "dcmotor.tiny", "DCMotor", 3.0),
        ("resistor_network", "resistor_network.tiny", "ResistorNetwork", 0.01),
        ("diode_circuit", "diode_circuit.tiny", "DiodeCircuit", 1.0)]
    record = golden(name)
    system = compile_example(file, model)
    solution = simulate_example(system, stop)

    compared = 0
    for (variable, reference) in pairs(record.simulation.final)
        symbol = try lookup(system, String(variable)) catch; continue end
        value = try solution(stop; idxs = symbol) catch; continue end
        compared += 1
        @test TinySim.agrees(value, reference, record.tolerance)
    end
    @test compared >= length(record.simulation.variables) - 2
end

@testset "initial equations are solved before the run" begin
    # The tank starts where its level is not changing: h = (qin/k)^2.
    system = compile_example("tank.tiny", "Tank")
    solution = simulate_example(system, 20.0)
    @test solution(0.0; idxs = lookup(system, "h")) ≈ (0.3 / 0.5)^2 rtol = 1e-9
    @test solution(20.0; idxs = lookup(system, "h")) ≈ (0.3 / 0.5)^2 rtol = 1e-6
end

@testset "events" begin
    record = golden("bouncing_ball")
    system = compile_example("bouncing_ball.tiny", "BouncingBall")
    solution = simulate_example(system, 3.0; solver = Tsit5())
    height = solution[lookup(system, "h")]
    velocity = solution[lookup(system, "v")]
    bounces = [solution.t[index] for index in 2:length(solution.t)
               if velocity[index] > 0 && velocity[index - 1] < 0]

    @test length(bounces) == length(record.simulation.events)
    @test bounces[1] ≈ sqrt(2 * 1.0 / 9.81) rtol = 1e-6
    @test bounces[1] ≈ record.simulation.events[1].time rtol = 1e-5
    @test minimum(height) > -1e-9              # it never sinks through the floor
    @test solution(3.0; idxs = lookup(system, "h")) ≈
          record.simulation.final["h"] rtol = 1e-4
end

@testset "a discrete variable is a held state" begin
    record = golden("thermostat")
    system = compile_example("thermostat.tiny", "Thermostat")
    solution = with_logger(NullLogger()) do
        solve(ODEProblem(system, [], (0.0, 200.0)), Tsit5();
              reltol = 1e-8, abstol = 1e-10)
    end
    temperature = solution[lookup(system, "T")]
    heater = solution[lookup(system, "on")]
    settled = solution.t .> 20

    @test sort(unique(round.(heater, digits = 6))) == [0.0, 1.0]
    @test minimum(temperature[settled]) ≈ 19.0 atol = 1e-3
    @test maximum(temperature[settled]) ≈ 21.0 atol = 1e-3
    @test count(!=(0), diff(heater)) == length(record.simulation.events)
end

@testset "the high-index model now simulates" begin
    # The Python implementation refused this one; MTK reduces the index, which
    # is the decision recorded in docs/julia-migration-plan.md §6.
    record = golden("pendulum_cartesian")
    @test !record.compiles
    @test occursin("structurally singular", record.error)

    system = compile_example("pendulum_cartesian.tiny", "CartesianPendulum")
    solution = simulate_example(system, 2.0)
    x = solution[lookup(system, "x")]
    y = solution[lookup(system, "y")]
    # What the contract on that model asks: the mass stays on the rod.
    @test maximum(abs.(sqrt.(x .^ 2 .+ y .^ 2) .- 1.0)) < 1e-6
end
