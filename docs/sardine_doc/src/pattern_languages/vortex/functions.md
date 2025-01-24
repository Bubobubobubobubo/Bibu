# Functions

**Vortex** contains most of the basic **TidalCycles** functions. Here is a comprehensive list of all the functions currently defined in the Python codebase. Take some time to discover them all.

## Concatenative operations

These functions typically take up to **n** patterns to form a single pattern that contains them all, making a sequence.


- `slowcat`: make a single pattern out of multiple patterns without speeding up. The pattern will thus span over multiple cycles.

```python
    d1 * slowcat(
      s('bd*2 sn'), s("jvbass*3"),
      s("drum*2"), s("ht mt")
    )
```

- `fastcat`: make a single pattern out of multiple patterns but mashing all of them into a single cycle. Be careful with long / rich patterns :)

```python
    d1 * fastcat(
      s('bd*2 sn'), s("jvbass*3"),
      s("drum*2"), s("ht mt")
    )
```

- `append`: synonym of `fastcat`.

- `timecat`: Like `fastcat` except that you provide proportionate sizes of the patterns to each other for when they're concatenated into one cycle. The larger the value in the list, the larger relative size the pattern takes in the final loop. If all values are equal then this is equivalent to `fastcat`.

```python
    d1 * timecat((1, s("bd*4")), (1, s("hh27*8"))))
```

- `randcat`: Pick patterns in a random order. 

```python
    d1 * randcat(
      s('bd*2 sn'), s("jvbass*3"),
      s("drum*2"), s("ht mt")
    )
```

- `struct`: Restructure the pattern according to a binary pattern (false values are dropped).

```python
    # add an example
```

- `stack`: Pile up patterns or `stack` patterns. Useful to group some patterns together.

```python
    # add an example
```

## Superposition and layering


- `jux`: a complex function. It takes your pattern, makes two versions of them, one playing on the left channel, the other on the right channel. It then applies a function but only on the right-hand channel.

```python
    # On the right channel: reversed and filtered
    d1 * s('bd jvbass bd sn').jux(lambda p : p.rev().hpf(500))
```

- `superimpose`: superpose a modified version of a pattern on the top of the original pattern. They are played at the same time, thus superimposed.

```python
    d1 * s('hh cp hh cp').superimpose(lambda p: p.fast(2).rev())
```

- `layer`: Layer up multiple functions on one pattern. For example, the following will play two versions of the pattern at the same time, one reversed and one at twice the speed.

```python
    d1 * s("arpy [~ arpy:4]").layer(rev, lambda p: p.fast(2))
```

If you want to include the original version of the pattern in the layering, use the `id` function:

```python
    d1 * s("arpy [~ arpy:4]").layer(id, rev, lambda p: p.fast(2))
```

## Pattern degradation

- `degrade`: randomly removes events from pattern, by 50% chance.

```python
    d1 * s('[bd(5,8), hh(7,8), sn(2,8)]').degrade()
```

- `degrade_by`: Simlar to `degrade` but you can control the percentage of events that are removed with `by`.


```python
    d1 * s('[bd(5,8), hh(7,8), sn(2,8)]').degrade_by(0.2)
```

- `undegrade`: Same as `degrade`, but random values represent percentage of events to keep, not remove, by 50% chance.

```python
    d1 * s('[bd(5,8), hh(7,8), sn(2,8)]').undegrade()
```
- `undegrade_by`: Similar to `degrade` but you can control the percentage of events that are removed with `by`.

```python
    d1 * s('[bd(5,8), hh(7,8), sn(2,8)]').undegrade_by(0.2)
```

## Sometimes family

The `sometimes` family of function can sometimes apply a function to a pattern... but only sometimes :) There is a large family of functions and helper functions to manipulate probabilities.

- `always`: will always apply the function, similar to not using the function and applying directly.

```python
    d1 * s('drum(5,8)').n('1 2 3 4').always(fast(2))
```

- `almostAlways` | `almost_always`: will apply the function 90% of the time, at random.

```python
    d1 * s('drum(5,8)').n('1 2 3 4').almostAlways(fast(2))
```

- `often`: will apply the function 75% of the time, at random.

```python
    d1 * s('drum(5,8)').n('1 2 3 4').often(fast(2))
```

- `sometimes`: Applies a function to pattern, around 50% of the time, at random.

```python
    d1 * s('drum(5,8)').n('1 2 3 4').sometimes(fast(2))
```

- `sometimes_by`: Applies a function to pattern sometimes based on specified `by` percentage, at random. `by` is a number between 0 and 1, representing 0% to 100% chance of applying function.

- `sometimes_pre`: Similar to `sometimes` but applies a function to the pattern *before* filtering events 50% of the time, at random.

- `rarely`: will apply the function 25% of the time, at random.

```python
    d1 * s('drum(5,8)').n('1 2 3 4').rarely(fast(2))
```

- `almostNever` | `almost_never`: will apply the function 10% of the time, at random.

```python
    d1 * s('drum(5,8)').n('1 2 3 4').almostNever(fast(2))
```

- `never`: will apply the function 0% of the time, at random. The function will thus never be applied.

```python
    d1 * s('drum(5,8)').n('1 2 3 4').never(fast(2))
```

## The every family and friends

- `every`: allows you to apply a function based on a condition. You need a function to determine when the function should be applied (*e.g.* a number **n** to apply the function every **n** cycles). You also need a transformation such as `rev` (reverse a pattern) or `fast` (make a pattern faster).

```python
    d1 * s('[bd(5,8), jvbass(3,8)]').every(3, lambda p: p.superimpose(fast(2)))
```

- `somecycles`: Apply a function on certain cycles (*e.g* on cycle `3`) ???

```python
    # add an example
```

- `somecycles_by`: Apply a function on certain cycles with a change factor ???

```python
    # add an example
```

## Operators

The Haskell version of **TidalCycles** uses a lot of overloaded operators. Python is not really flexible in that regard. You can't really define new operators such as `$` or `|+` but most of the base operators have been overloaded to denote a particular pattern transformation. They are used to denote a particular application of a pattern over another pattern.

- `+` 
- `-`
- `*`
- `/`
- `//`
- `**`
- `>>`
- `<<`

# Time manipulation

- `rev`: This function can be used to reverse a pattern. Very useful!

```python
    # Normal
    d1 * s('bd hh sn hh')

    # Reversed
    d1 * s('bd hh sn hh').rev()
```

- `fast`: `fast` is a function used to speed up a pattern. `fast(2)` will play the pattern twice as fast. `fast(0.5)` will play the pattern twice as slow. You can pattern the value as well.

```python
    d1 * s('bd hh sn hh').fast(2)
```

- `slow`: `slow` is a function used to slow down a pattern. It is a mirror of `fast`. `slow(2)` will play the pattern twice as slow, `slow(0.5)` will play a pattern twice as fast.

```python
    d1 * s('bd hh sn hh').slow(2)
```

- `early`: Equivalent of **Tidal**'s `<~` operator. This function can shift an event *earlier* in time, nudging it a little bit before the moment where it was initially supposed to play.

```python
    d1 * s('[tabla drum] cp').early('0.2 0.1')
```

- `late`: Equivalent of **Tidal**'s `~>` operator. This function can shift an event *later* in time, nudging it a little bit after the moment where it was initially supposed to play.

```python
    d1 * s('[tabla drum] bd cp').late('0.2 0.1')
```

- `off`: Combination of the `stack` function with `early`. Will play the same pattern twice, one version of it will be played slightly earlier.

- `compress`: Squeeze pattern within the specified time span.

```python
    # add an example
```

- `fastgap`: Similar to `fast` but maintains its cyclic alignment. For example, `p.fastgap(2)` would squash the events in pattern `p` into the first half of each cycle (and the second halves would be empty). The factor should be at least 1.

```python
    # add an example
```

## Conditional application

- `when`: Applies function `func` on each event of pattern if `boolean_pat` is true. You will have to feed a pattern of boolean values (`1` or `0`) using another function.

```python
    # add an example
```

- `when_cycle`: Applies function `func` to pattern only if `test_func` returns `True` on each cycle. Similar to `when`, but instead of working with a boolean pattern, this evaluates a boolean function with the cycle number and applies (or not) transformation on each cycle.

```python
    # add an example
```

## Iteration over patterns

- `iter`: Divides a pattern into a given number of subdivisions, plays the subdivisions in order, but increments the starting subdivision each cycle. The pattern wraps to the first subdivision after the last subdivision is played.

```python
    d1 * s("bd hh sn cp").iter(4)
```

- `reviter`: Same as `iter` but in the other direction.

```python
    d1 * s("bd hh sn cp").reviter(4)
```



- `striate`


- `mask`
- `euclid`

## Signals and Generators

Signal functions are functions generating streams of values to apply to a pattern. They are very useful to create low-frequency oscillators or continuously evolving stream of values. These functions should be manipulated to your liking using `segment` and `range`: scaling the stream of generated values, getting the desired granularity/precision of the stream).

### Signal manipulation functions

- `range`: Rescales values to the range `[min, max]`. Assumes pattern is numerical, containing unipolar values in the range `[0, 1]`.
- `rangex`: Rescales values to the range [min, max] following an exponential curve. Assumes pattern is numerical, containing unipolar values in the range `[0, 1]`.
- `segment`: Samples the pattern at a rate of `n` events per cycle. Useful for turning a continuous pattern into a discrete one.

```python
    sine().segment(8).range(4, 8)
```

### Generators

- `sine2`: Bipolar sinusoïdal oscillator: negative and positive values.
- `sine`: Unipolar sinusoïdal oscillator. 
- `cosine2`: Similar to `sine2` but the phase is shifted (*e.g.* cosinus function).
- `cosine`: Similar to `sine` but the phase is shifted (*e.g.* cosinus function).
- `saw2`: Bipolar sawtooth-like oscillator: negative and positive values. 
- `saw`: Unipolar sawtooth-like oscillator.
- `isaw2`: Inverted Bipolar sawtooth-like function.
- `isaw`: Inverted unipolar sawtooth-like function.
- `tri2`: Bipolar triangular oscillator: negative and positive values.
- `tri`: Unipolar triangular oscillator.
- `square2`: Bipolar square wave oscillator: negative and positive values.
- `square`: Unipolar square wave oscillator.
- `rand`: Generate a continuous pattern of pseudo-random numbers between `0` and `1`. 
- `irand`: Generate a pattern of pseudo-random whole numbers between `0` to `n-1` inclusive.

```python
    # generates a pattern of 8 events per cycle
    # with values ranging from 0 to 15 inclusive.
    irand(16).segment(8)
```

- `perlin`: 1D Perlin (smooth) noise, works like `rand` but smoothly moves between random values each cycle.

```python
    perlin().segment(8)
```

## Combinators

- `run`
- `scan`

- `choose_with`
- `choose`
- `choose_cycles`
- `wchoose`
