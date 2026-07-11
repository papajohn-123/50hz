import Foundation

struct GridTimelineSampler: Sendable {
    let timeline: GridTimeline

    func sample(at date: Date) -> GridTimelineSample? {
        let samples = timeline.samples.sorted { $0.timestamp < $1.timestamp }
        guard let first = samples.first, let last = samples.last else { return nil }
        if date <= first.timestamp { return first }
        if date >= last.timestamp { return last }

        guard let upperIndex = samples.firstIndex(where: { $0.timestamp >= date }), upperIndex > 0 else {
            return nil
        }

        let lower = samples[upperIndex - 1]
        let upper = samples[upperIndex]
        let interval = upper.timestamp.timeIntervalSince(lower.timestamp)

        guard interval > 0, interval <= Double(timeline.materialGapSeconds) else {
            return date.timeIntervalSince(lower.timestamp) < upper.timestamp.timeIntervalSince(date) ? lower : upper
        }

        // Forecast/observed boundaries are semantic and stay stepwise.
        guard lower.factClass == upper.factClass else {
            return date < timeline.nowBoundary ? lower : upper
        }

        let progress = date.timeIntervalSince(lower.timestamp) / interval
        return GridTimelineSample(
            timestamp: date,
            factClass: lower.factClass,
            demandMW: interpolate(lower.demandMW, upper.demandMW, progress),
            carbonIntensity: interpolate(lower.carbonIntensity, upper.carbonIntensity, progress),
            frequencyHz: interpolateOptional(lower.frequencyHz, upper.frequencyHz, progress),
            generation: interpolateGeneration(lower.generation, upper.generation, progress)
        )
    }

    private func interpolate(_ lower: Double, _ upper: Double, _ progress: Double) -> Double {
        lower + ((upper - lower) * progress)
    }

    private func interpolateOptional(_ lower: Double?, _ upper: Double?, _ progress: Double) -> Double? {
        guard let lower, let upper else { return lower ?? upper }
        return interpolate(lower, upper, progress)
    }

    private func interpolateGeneration(
        _ lower: [FuelReading],
        _ upper: [FuelReading],
        _ progress: Double
    ) -> [FuelReading] {
        let lowerByFuel = Dictionary(uniqueKeysWithValues: lower.map { ($0.fuel, $0) })
        let upperByFuel = Dictionary(uniqueKeysWithValues: upper.map { ($0.fuel, $0) })
        let fuels = FuelKind.allCases.filter { lowerByFuel[$0] != nil || upperByFuel[$0] != nil }

        return fuels.map { fuel in
            let reading = lowerByFuel[fuel] ?? zeroReading(for: fuel, basedOn: upperByFuel[fuel]!)
            let next = upperByFuel[fuel] ?? zeroReading(for: fuel, basedOn: reading)
            return FuelReading(
                fuel: fuel,
                megawatts: interpolate(reading.megawatts, next.megawatts, progress),
                share: interpolate(reading.share, next.share, progress),
                changeOneHour: reading.changeOneHour,
                rank: progress < 0.5 ? reading.rank : next.rank,
                factClass: reading.factClass
            )
        }
    }

    private func zeroReading(for fuel: FuelKind, basedOn reading: FuelReading) -> FuelReading {
        FuelReading(
            fuel: fuel,
            megawatts: 0,
            share: 0,
            changeOneHour: reading.changeOneHour,
            rank: reading.rank,
            factClass: reading.factClass
        )
    }
}
