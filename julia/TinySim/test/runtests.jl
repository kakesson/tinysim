using Test
using TinySim

# Phase 0 has one deliverable: the golden files that define what the port must
# reproduce. These tests check that the oracle is complete and well formed, and
# they pin the handful of numbers that matter most -- the ones a physicist
# would check by hand. Every later phase adds tests that *compare against*
# these files; this one tests the files themselves.

const EXPECTED = ["bouncing_ball", "dcmotor", "diode_circuit", "electrical",
                  "pendulum", "pendulum_cartesian", "resistor_network", "tank",
                  "thermostat"]

@testset "TinySim" begin
    @testset "the oracle covers every example" begin
        @test golden_names() == EXPECTED
    end

    @testset "$name is a complete record" for name in EXPECTED
        record = golden(name)
        @test haskey(record, :model)
        @test haskey(record, :tolerance)
        @test record.tolerance.relative > 0

        if !record.compiles
            # The Cartesian pendulum: high index, and today refused. MTK will
            # simulate it, so this record is what the refusal used to say.
            @test occursin("structurally singular", record.error)
            @test !isempty(record.flat.equations)
            continue
        end

        @test !isempty(record.flat.equations)
        @test record.flat.equation_count == length(record.flat.equations)
        @test record.flat.equation_count == record.flat.continuous_count
        @test !isempty(record.blocks)
        @test !isempty(record.simulation.samples)
        @test !isempty(record.contracts)

        times = [sample.time for sample in record.simulation.samples]
        @test issorted(times)
        @test all(isfinite, times)
        for sample in record.simulation.samples, value in values(sample.values)
            @test isfinite(value)
        end

        for block in record.blocks
            @test block.method in ("explicit", "symbolic", "linear system", "newton")
            @test !isempty(block.unknowns)
        end

        for contract in record.contracts
            @test contract.verdict in ("satisfied", "violated", "not tested")
            @test !isempty(contract.clauses)
            for clause in contract.clauses
                @test clause.kind in ("assume", "guarantee")
                @test isfinite(clause.margin)
            end
        end
    end

    @testset "the numbers the port has to hit" begin
        # An RC circuit charging: V(1 - exp(-t/RC)) at t = 1 s.
        rc = golden("electrical")
        @test rc.flat.equation_count == 20
        @test length(rc.blocks) == 4
        @test TinySim.agrees(rc.simulation.final["c.v"], 10 * (1 - exp(-1 / 0.1)),
                             rc.tolerance)

        # The bouncing ball: first bounce at sqrt(2h/g), six of them in 3 s.
        ball = golden("bouncing_ball")
        @test length(ball.simulation.events) == 6
        @test TinySim.agrees(ball.simulation.events[1].time, sqrt(2 * 1.0 / 9.81),
                             ball.tolerance)

        # The thermostat: 209 switches, and the temperature stays in the band.
        thermostat = golden("thermostat")
        @test length(thermostat.simulation.events) == 209

        # The DC motor, three seconds in: on its way to the steady state
        # V*k / (k^2 + R*d) = 160 rad/s, and within a percent of it.
        motor = golden("dcmotor")
        @test motor.simulation.final["load.w"] ≈ 24 * 0.1 / (0.1^2 + 0.5 * 0.01) rtol = 0.01

        # The tank, started in steady state at (qin/k)^2.
        tank = golden("tank")
        @test TinySim.agrees(tank.simulation.samples[1].values["h"], (0.3 / 0.5)^2,
                             tank.tolerance)

        # Every contract of every example was satisfied when it was recorded.
        for name in EXPECTED
            record = golden(name)
            record.compiles || continue
            for contract in record.contracts
                @test contract.verdict == "satisfied"
            end
        end
    end
end
