# What the language gained for time-discrete controllers, end to end:
# sampling, sequential when bodies, records, and automata.
#
# Every number here is one that can be worked out by hand, because the point of
# these constructs is that the software they describe is the software that runs.

using ModelingToolkit, OrdinaryDiffEq, Logging

function compile_source(source, model)
    with_logger(NullLogger()) do
        mtkcompile(TinySim.build(TinySim.parse(source), model))
    end
end

function run_source(source, model, stop; solver = Tsit5(), kwargs...)
    system = compile_source(source, model)
    solution = with_logger(NullLogger()) do
        solve(ODEProblem(system, [], (0.0, stop)), solver; reltol = 1e-10,
              abstol = 1e-12, kwargs...)
    end
    return system, solution
end

const SAMPLED_PI = """
record ControllerState "what the controller carries between ticks"
  discrete Real integral(start = 0);
  discrete Real previousError(start = 0);
end ControllerState;

model SampledPI
  parameter Real Ts = 0.05, Kp = 2, Ki = 5, tau = 0.5, reference = 1;
  Real y(start = 0);
  discrete Real u(start = 0);
  discrete Real e(start = 0);
  ControllerState s;
equation
  der(y) = (-y + u) / tau;
  when sample(0, Ts) then
    e := reference - y;
    s.integral := pre(s.integral) + Ki * Ts * e;
    s.previousError := e;
    u := Kp * e + s.integral;
  end;
end SampledPI;
"""

@testset "sampling" begin
    system, solution = run_source(SAMPLED_PI, "SampledPI", 2.0)

    # The control signal is held between ticks: 2 s at 50 ms is 40 of them.
    control = [solution(moment; idxs = system.u) for moment in 0:0.001:2]
    @test length(unique(round.(control, digits = 9))) == 40

    # And the loop does its job.
    @test solution(2.0; idxs = system.y) ≈ 1.0 rtol = 0.02
end

@testset "a when body runs in order" begin
    # At the first tick the error is 1, so the new integral is Ki*Ts*e = 0.25
    # and u = Kp*e + integral = 2.25. Reading the *old* integral would give 2.0,
    # which is what simultaneous equations would produce -- and what the Python
    # implementation does.
    system, solution = run_source(SAMPLED_PI, "SampledPI", 0.02)
    @test solution(0.01; idxs = system.s.integral) ≈ 0.25 rtol = 1e-9
    @test solution(0.01; idxs = system.u) ≈ 2.25 rtol = 1e-9
end

@testset "records are fields under a dotted name" begin
    system, solution = run_source(SAMPLED_PI, "SampledPI", 1.0)
    @test solution(1.0; idxs = system.s.integral) isa Real
    @test solution(1.0; idxs = system.s.previousError) ≈
          solution(1.0; idxs = system.e) rtol = 1e-9

    # A record has no equations, and saying otherwise is an error.
    @test_throws TinySim.TinySimSyntaxError TinySim.parse(
        "record R Real x; equation x = 1; end R;")
end

const SUPERVISOR = """
automaton SupervisorLogic sampled at 0.01
  parameter Real wTarget = 50, uStart = 5, startTimeout = 2, gain = 20;
  Real w, request;
  discrete Real command(start = 0);
  state Off, Starting, Running, Fault;
  initial Off;
transition
  Off      -> Starting when request > 0.5 then command := uStart; end;
  Starting -> Running  when w > wTarget;
  Starting -> Fault    when timeInState > startTimeout then command := 0; end;
  Fault    -> Off      when request < 0.5 then command := 0; end;
end SupervisorLogic;

model Machine
  parameter Real gain = 20;
  SupervisorLogic supervisor;
  Real w(start = 0);
equation
  supervisor.w = w;
  supervisor.request = if time > 0.5 then 1 else 0;
  der(w) = -0.5 * w + gain * supervisor.command;
end Machine;
"""

@testset "an automaton takes its transitions in order, on its own clock" begin
    system, solution = run_source(SUPERVISOR, "Machine", 6.0)
    state(moment) = round(Int, solution(moment; idxs = system.supervisor.state))
    command(moment) = solution(moment; idxs = system.supervisor.command)

    @test state(0.0) == 1                       # Off
    @test state(0.49) == 1                      # the request is still low
    @test state(0.51) == 2                      # Starting, at the first tick after it rose
    @test command(0.51) ≈ 5.0                   # and the entry action ran
    @test state(6.0) == 3                       # Running: w passed the target

    # `timeInState` restarts at every transition.
    @test solution(0.49; idxs = system.supervisor.timeInState) ≈ 0.49 atol = 0.011
    @test solution(0.51; idxs = system.supervisor.timeInState) < 0.011
end

@testset "a timeout is a guard on timeInState" begin
    # With a feeble actuator the speed never reaches the target, so the machine
    # sits in Starting until the timeout takes it to Fault -- two seconds after
    # it entered, and not before.
    feeble = replace(SUPERVISOR, "parameter Real gain = 20;" => "parameter Real gain = 1;")
    system, solution = run_source(feeble, "Machine", 6.0)
    state(moment) = round(Int, solution(moment; idxs = system.supervisor.state))

    @test state(1.0) == 2                       # still Starting
    @test state(2.4) == 2                       # the timeout has not expired
    @test state(2.6) == 4                       # Fault, two seconds after entry
    @test solution(3.0; idxs = system.supervisor.command) ≈ 0.0
end

@testset "the state is readable as a constant" begin
    source = replace(SUPERVISOR,
        "der(w) = -0.5 * w + gain * supervisor.command;" =>
        "der(w) = if supervisor.state == supervisor.Running then 0 " *
        "else -0.5 * w + gain * supervisor.command;")
    system, solution = run_source(source, "Machine", 6.0)
    # Once it reaches Running the speed is frozen, which only happens if the
    # comparison against the state constant works.
    @test solution(3.0; idxs = system.w) ≈ solution(6.0; idxs = system.w) rtol = 1e-6
end

@testset "an automaton may not shadow what it owns" begin
    source = """
    automaton A sampled at 0.01
      discrete Real state(start = 0);
      state Off, On;
      initial Off;
    transition
      Off -> On when time > 1;
    end A;
    model M A a; Real x(start = 0); equation der(x) = 1; end M;
    """
    @test_throws Union{TinySim.ModelError, TinySim.TinySimSyntaxError} compile_source(source, "M")
end
