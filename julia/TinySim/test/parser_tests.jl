# Phase 1: the front end. These tests are the Julia counterpart of
# tests/test_parser.py, plus the constructs the language gained for
# time-discrete controllers: sampling, sequential when bodies, if statements,
# records and automata.

const EXAMPLES = normpath(joinpath(@__DIR__, "..", "..", "..", "examples"))

parse_source(source) = TinySim.parse(source)

@testset "lexer" begin
    tokens = TinySim.tokenize("// hello\n/* and\n   more */ model A \"text\";")
    @test [token.kind for token in tokens] ==
          [:identifier, :identifier, :string, :operator, :eof]
    @test tokens[3].text == "text"          # the quotes are stripped
    @test tokens[1].line == 3               # line numbers survive block comments

    operators = TinySim.tokenize("a := b; c -> d; x[1]")
    @test any(token -> token.text == ":=", operators)
    @test any(token -> token.text == "->", operators)
    @test any(token -> token.text == "[", operators)

    @test_throws TinySim.TinySimSyntaxError TinySim.tokenize("model A\n  Real x @ 1;\nend A;")
end

@testset "declarations" begin
    program = parse_source("""
        model M
          parameter Real R = 100 "ohm";
          Real v(start = 2), i;
        end M;
        """)
    declarations = program.classes[1].declarations
    @test [d.name for d in declarations] == ["R", "v", "i"]
    @test declarations[1].prefixes == [:parameter]
    @test declarations[1].description == "ohm"
    @test TinySim.to_source(declarations[1].value) == "100"
    @test TinySim.to_source(declarations[2].modifiers["start"]) == "2"

    nested = parse_source("""
        connector P Real v; end P;
        model C P p; Real v; end C;
        model M C c(v(start = 3), p(v(start = 1))); end M;
        """)
    modifiers = nested.classes[3].declarations[1].modifiers
    @test TinySim.to_source(modifiers["v"]["start"]) == "3"
    @test TinySim.to_source(modifiers["p"]["v"]["start"]) == "1"
end

@testset "expressions" begin
    program = parse_source("model M Real y, x; equation y = 1 + 2 * x ^ 2 - -x; end M;")
    equation = program.classes[1].equations[1]
    @test TinySim.to_source(equation.right) == "1 + 2 * x^2 - -x"

    conditional = parse_source("model M Real y, x; equation y = if x > 0 then x else -x; end M;")
    @test TinySim.to_source(conditional.classes[1].equations[1].right) ==
          "if x > 0 then x else -x"

    @test_throws TinySim.TinySimSyntaxError parse_source(
        "model A Real x; equation x = sinn(1); end A;")
    @test_throws TinySim.TinySimSyntaxError parse_source(
        "model A Real x, y; equation der(x + y) = 1; x = 0; end A;")
end

@testset "when bodies are software" begin
    program = parse_source("""
        model M
          Real x(start = 0);
          discrete Real u(start = 0), e(start = 0);
        equation
          der(x) = u;
          when sample(0, 0.1) then
            e := 1 - x;
            u := 2 * e;
            if u > 1 then
              u := 1;
            elseif u < -1 then
              u := -1;
            else
              e := e;
            end if;
          end;
        end M;
        """)
    when_equation = program.classes[1].equations[2]
    @test when_equation.condition isa TinySim.SampleCondition
    @test TinySim.to_source(when_equation.condition.interval) == "0.1"
    @test length(when_equation.body) == 3
    @test when_equation.body[1] isa TinySim.Assignment
    branch = when_equation.body[3]
    @test branch isa TinySim.IfStatement
    @test length(branch.conditions) == 2
    @test length(branch.otherwise) == 1

    # `=` inside a body is refused, and the message says why.
    caught = try
        parse_source("model M discrete Real u(start=0); Real x(start=0); equation " *
                     "der(x) = u; when time > 1 then u = 1; end; end M;")
        nothing
    catch error
        error
    end
    @test caught isa TinySim.TinySimSyntaxError
    @test occursin(":=", sprint(showerror, caught))

    @test_throws TinySim.TinySimSyntaxError parse_source(
        "model A discrete Real x(start=0); equation when time > 1 then x := 0; end when; end A;")
end

@testset "records" begin
    program = parse_source("""
        record ControllerState "carried between ticks"
          discrete Real integral(start = 0);
          discrete Real previousError(start = 0);
        end ControllerState;
        """)
    record = program.classes[1]
    @test record.kind === :record
    @test record.description == "carried between ticks"
    @test [d.name for d in record.declarations] == ["integral", "previousError"]

    caught = try
        parse_source("record R Real x; equation x = 1; end R;")
        nothing
    catch error
        error
    end
    @test caught isa TinySim.TinySimSyntaxError
    @test occursin("no equations", sprint(showerror, caught))
end

@testset "automata" begin
    program = parse_source("""
        automaton Supervisor sampled at 0.01 "mode logic"
          state Off, Starting, Running, Fault;
          initial Off;
        transition
          Off      -> Starting when startCommand > 0.5 then u := uStart; end;
          Starting -> Running  when w > wTarget;
          Starting -> Fault    when timeInState > startTimeout;
          Fault    -> Off      when resetCommand > 0.5 then u := 0; end;
        end Supervisor;
        """)
    automaton = program.automata[1]
    @test automaton.states == ["Off", "Starting", "Running", "Fault"]
    @test automaton.initial == "Off"
    @test TinySim.to_source(automaton.rate) == "0.01"
    @test length(automaton.transitions) == 4
    @test automaton.transitions[1].from == "Off"
    @test automaton.transitions[1].to == "Starting"
    @test length(automaton.transitions[1].actions) == 1
    @test isempty(automaton.transitions[2].actions)
    # the guard of the third transition is a timeout
    @test occursin("timeInState", TinySim.to_source(automaton.transitions[3].guard))

    for bad in ["automaton A sampled at 0.1 state X; initial Y; transition end A;",
                "automaton A sampled at 0.1 state X; initial X; transition X -> Z when time > 1; end A;"]
        @test_throws TinySim.TinySimSyntaxError parse_source(bad)
    end
end

@testset "contracts" begin
    program = parse_source("""
        model M Real x(start = 0); equation der(x) = 1; end M;
        contract C for M "what it promises"
        assume
          always x >= 0;
        guarantee
          eventually within [0, 2] x > 1;
          whenever x > 1 then x > 0 within [0, 1];
          x stays within [0, 3];
          after 2 always x > 1;
          never x > 99;
          x settles to 3 within 0.1 after 2;
        end C;
        """)
    contract = program.contracts[1]
    @test contract.name == "C"
    @test contract.model_name == "M"
    @test contract.description == "what it promises"
    @test length(contract.assumptions) == 1
    @test length(contract.guarantees) == 6

    written = [TinySim.to_source(clause.formula) for clause in contract.guarantees]
    @test written[1] == "eventually within [0, 2] x > 1"
    @test written[2] == "whenever x > 1 then x > 0 within [0, 1]"
    @test written[3] == "x stays within [0, 3]"
    @test written[5] == "never x > 99"

    # A temporal operator scopes over everything after it.
    wide = parse_source("model M Real x; equation x = 1; end M;\n" *
                        "contract C for M guarantee always x > 0 and x < 9; end C;")
    @test TinySim.to_source(wide.contracts[1].guarantees[1].formula) ==
          "always (x > 0 and x < 9)"

    @test_throws TinySim.TinySimSyntaxError parse_source(
        "model M Real x; equation x = 1; end M;\ncontract C for M guarantee always x; end C;")
end

@testset "every example parses" begin
    expected = Dict("bouncing_ball.tiny" => (1, 1), "dcmotor.tiny" => (10, 3),
                    "diode_circuit.tiny" => (8, 1), "electrical.tiny" => (8, 2),
                    "pendulum.tiny" => (1, 1), "pendulum_cartesian.tiny" => (1, 1),
                    "resistor_network.tiny" => (7, 1), "tank.tiny" => (1, 1),
                    "thermostat.tiny" => (1, 1))
    for (name, (classes, contracts)) in sort(collect(expected))
        program = TinySim.parse_file(joinpath(EXAMPLES, name))
        @test length(program.classes) == classes
        @test length(program.contracts) == contracts
    end
end
